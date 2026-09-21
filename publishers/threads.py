"""
Meta Threads 자동 발행 Publisher

Meta 공식 Threads API (graph.threads.net/v1.0) 사용.
- 장기 액세스 토큰 기반 (만료 60일, 갱신 가능)
- TEXT / IMAGE 포스트 지원
- 2단계: 컨테이너 생성 → 발행

환경변수:
    THREADS_USER_ID       : Threads 사용자 ID (숫자)
    THREADS_ACCESS_TOKEN  : 장기 액세스 토큰
"""
import os
import time
import urllib.parse
from functools import wraps
from typing import Optional

import requests

from common.logger import log
from common.threads_access import blocked_reason, observe_error, suspend
from .base import Publisher, PostResult


GRAPH_BASE = "https://graph.threads.net/v1.0"


def _serialized_publish(func):
    @wraps(func)
    def wrapped(self, *args, **kwargs):
        reason = blocked_reason()
        if reason:
            log(reason, "warn")
            return PostResult(success=False, message=reason)
        try:
            from common.threads_guard import publication_lock
            with publication_lock():
                return func(self, *args, **kwargs)
        except Exception as exc:
            log(f"Threads 발행 중단: {type(exc).__name__}", "error")
            return PostResult(success=False, message=f"Threads 발행 중단: {type(exc).__name__}")
    return wrapped


class ThreadsPublisher(Publisher):
    """Meta Threads 공식 API 발행기."""

    def __init__(self,
                 user_id: Optional[str] = None,
                 access_token: Optional[str] = None):
        self.user_id      = user_id      or os.getenv("THREADS_USER_ID", "")
        self.access_token = access_token or os.getenv("THREADS_ACCESS_TOKEN", "")

    # ------------------------------------------------------------------
    # Publisher interface
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """환경변수에서 자격증명 로드 확인."""
        reason = blocked_reason()
        if reason:
            log(reason, "warn")
            return False
        if not self.user_id or not self.access_token:
            log("THREADS_USER_ID 또는 THREADS_ACCESS_TOKEN 미설정", "error")
            return False
        log(f"Threads API 준비 (user_id={self.user_id[:6]}...)", "ok")
        return True

    @_serialized_publish
    def post(self, title: str, content: str,
             tags: list[str] = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        """Threads 포스트 발행.

        Args:
            title    : 제목 (content 앞에 붙임)
            content  : 본문
            tags     : 해시태그 목록
            image_url: 이미지 URL (있으면 IMAGE 타입으로 발행)
        """
        # 본문 조합
        text = f"{title}\n\n{content}" if title else content

        # 해시태그 추가
        if tags:
            hashtags = " ".join(
                f"#{t.lstrip('#')}" for t in tags[:5]
            )
            text = f"{text}\n\n{hashtags}"

        # Threads 글자 수 제한 500자
        if len(text) > 500:
            text = text[:497] + "..."

        log(f"Threads 발행 준비: {text[:60]}...", "step")

        # Step 0: 발행 게이트 (품질·발행량·사람 승인)
        meta = self._meta_line(kwargs, category=category, image_url=image_url)
        ok, reason, is_affiliate = self._gate(text, meta)
        if not ok:
            return PostResult(success=False, message=reason)

        # 미디어 타입
        media_type = "IMAGE" if image_url else "TEXT"

        # Step 1: 컨테이너 생성
        container_id = self._create_container(text, media_type, image_url)
        if not container_id:
            return PostResult(success=False, message="컨테이너 생성 실패")

        # Step 2: 잠시 대기 (API 권장)
        time.sleep(2)

        # Step 3: 발행
        result = self._publish_container(container_id)
        if result.success:
            self._record(is_affiliate)
        return result

    def upload_image(self, local_path: str) -> str:
        """로컬 이미지 업로드 미지원 — 공개 URL 직접 전달 방식 사용."""
        log("Threads는 공개 이미지 URL 직접 전달 방식 사용", "warn")
        return ""

    def get_categories(self) -> list[str]:
        return []

    # ------------------------------------------------------------------
    # Threads API 내부 메서드
    # ------------------------------------------------------------------

    def post_reply(self, parent_post_id: str, text: str) -> PostResult:
        """Reply chains remain disabled under the remediation plan."""
        return PostResult(success=False, message="Threads 연속 답글 발행 중단")

    # ------------------------------------------------------------------
    # 발행 게이트 (Meta 플랫폼 약관 7.e.i.2 시정 조치)
    # ------------------------------------------------------------------

    @staticmethod
    def _meta_line(kwargs: dict, category: str = "", image_url: str = "") -> str:
        """승인 메시지 상단에 붙일 한 줄 요약 — 운영자가 맥락을 보고 판단하도록."""
        bits = []
        for key in ("source", "keyword", "pipeline"):
            val = kwargs.get(key)
            if val:
                bits.append(f"{key}={val}")
        if category:
            bits.append(f"category={category}")
        if image_url:
            bits.append("image=Y")
        return " / ".join(bits) if bits else "(메타 정보 없음)"

    def _gate(self, text: str, meta: str = "") -> tuple[bool, str, bool]:
        """발행 직전 게이트. (통과여부, 차단사유, 제휴여부) 반환.

        순서가 중요하다 — 품질·발행량을 먼저 보고 사람 승인을 마지막에 묻는다.
        기계가 걸러낼 수 있는 건을 운영자에게 들이밀면 승인 피로가 쌓이고,
        결국 게이트를 꺼버리게 된다.

        Publisher 진입점에 두는 이유는 이곳이 모든 Threads 발행이 반드시
        통과하는 유일한 지점이기 때문이다. 파이프라인마다 넣으면 새 파이프라인이
        누락시킬 수 있다.
        """
        try:
            from common.threads_guard import precheck
            from common.threads_approval import gate as approval_gate
        except Exception as e:
            # 게이트를 불러오지 못하면 발행하지 않는다. 여기서 통과시키면
            # import 하나가 깨지는 것만으로 무인 발행으로 되돌아간다.
            log(f"Threads 게이트 로드 실패 — 발행 중단: {e}", "error")
            return False, "발행 게이트 로드 실패", False

        ok, reason, is_affiliate = precheck(text)
        if not ok:
            log(f"Threads 발행 차단 (게이트): {reason}", "error")
            return False, f"발행 게이트 차단: {reason}", is_affiliate

        ok, reason = approval_gate(text, meta)
        if not ok:
            log(f"Threads 발행 보류 (승인): {reason}", "error")
            return False, f"발행 승인 없음: {reason}", is_affiliate

        # Approval can span a date boundary or an operator suspension.
        reason = blocked_reason()
        if reason:
            return False, reason, is_affiliate
        return precheck(text)

    @staticmethod
    def _record(is_affiliate: bool) -> None:
        """발행 성공을 일일 카운터에 반영 (실패해도 발행을 되돌리지 않는다)."""
        try:
            from common.threads_guard import record_published
            record_published(is_affiliate)
        except Exception as e:
            log(f"Threads 발행량 기록 실패: {type(e).__name__}", "error")
            try:
                suspend("Published post could not be recorded; reconcile quota before resuming")
            except OSError:
                # The process hold is set before persistence; preserve the real
                # successful publication result so the caller does not repost it.
                log("Threads API 중지 상태 저장 실패 — 운영자 확인 필요", "error")

    def _alert_fatal_api_error(self, body: str) -> None:
        """API 오류 본문에서 '사람이 개입해야만 풀리는' 상태만 골라 텔레그램 알림.

        발행 실패의 대부분(rate-limit, 이미지 URL 거부, 본문 길이)은 다음 슬롯에
        자동 회복되므로 전부 알리면 소음이 된다. 반대로 아래 두 상태는 개입 전까지
        모든 슬롯이 100% 실패하는데, 그동안 stderr 에는 '컨테이너 생성 실패' 만
        남아 원인이 드러나지 않았다 (2026-08-18~08-25, 70건 연속 무성 실패).

        notify_login_required 가 24시간 throttle 을 이미 갖고 있어 슬롯마다
        중복 발송되지 않는다.
        """
        observe_error(body)
        low = (body or "").lower()
        if "api access deactivated" in low:
            platform = "Threads (Meta 앱 API 접근 차단)"
            reason = "Meta 가 앱의 API 접근을 비활성화했습니다. 토큰 문제가 아닙니다."
            app_id = os.getenv("THREADS_APP_ID", "").strip()
            target = (f"https://developers.facebook.com/apps/{app_id}" if app_id
                      else "https://developers.facebook.com/apps (앱 목록)")
            instructions = (
                f"{target} 에서 앱 상태·정책 경고 확인 "
                "(토큰 재발급으로는 복구되지 않음)"
            )
        elif "session has expired" in low or "invalid oauth access token" in low:
            platform = "Threads (액세스 토큰 만료)"
            reason = "장기 액세스 토큰이 만료됐습니다."
            instructions = "python -m common.threads_token oauth 로 재발급"
        else:
            return

        try:
            from common.notifier import notify_login_required
            notify_login_required(platform, instructions, reason=reason)
        except Exception:
            # 알림 실패가 발행 경로를 막아선 안 된다 (notifier 는 fire-and-forget).
            pass

    def _create_container(self, text: str, media_type: str,
                           image_url: str = "",
                           reply_to_id: str = "") -> Optional[str]:
        """Step 1: 미디어 컨테이너 생성.

        POST /v1.0/{user_id}/threads
        reply_to_id 가 있으면 부모 게시물에 답글로 달림.
        """
        if blocked_reason() or reply_to_id:
            return None
        url = f"{GRAPH_BASE}/{self.user_id}/threads"
        params: dict = {
            "media_type":   media_type,
            "text":         text,
            "access_token": self.access_token,
        }
        if media_type == "IMAGE" and image_url:
            params["image_url"] = image_url
        if reply_to_id:
            params["reply_to_id"] = reply_to_id

        try:
            resp = requests.post(url, data=params, timeout=15)
            if resp.ok:
                container_id = resp.json().get("id", "")
                log(f"Threads 컨테이너 생성 완료: {container_id}", "ok")
                return container_id
            log(f"Threads 컨테이너 생성 실패 ({resp.status_code}): {resp.text[:200]}", "error")
            self._alert_fatal_api_error(resp.text)
            return None
        except Exception as e:
            log(f"Threads 컨테이너 생성 예외: {e}", "error")
            return None

    def _publish_container(self, container_id: str) -> PostResult:
        """Step 2: 컨테이너 발행.

        POST /v1.0/{user_id}/threads_publish

        주의: 응답의 `id` 는 18자리 numeric ID 라 https://www.threads.net/t/<id>
        형식 URL 로는 안 열린다 (404). 정식 URL 은 별도 fields=permalink GET
        호출로 받아야 한다 (shortcode 또는 @username/post/<id> 형식).
        """
        reason = blocked_reason()
        if reason:
            return PostResult(success=False, message=reason)
        url = f"{GRAPH_BASE}/{self.user_id}/threads_publish"
        params = {
            "creation_id":  container_id,
            "access_token": self.access_token,
        }
        try:
            resp = requests.post(url, data=params, timeout=15)
            if not resp.ok:
                log(f"Threads 발행 실패 ({resp.status_code}): {resp.text[:200]}", "error")
                self._alert_fatal_api_error(resp.text)
                return PostResult(success=False, message=resp.text[:200])

            post_id = resp.json().get("id", "")
            if not post_id:
                return PostResult(success=False, message="threads_publish 응답에 id 없음")

            # 정식 permalink 조회 — 안 되면 numeric URL 폴백 (불완전하지만 ID 보존)
            post_url = self._fetch_permalink(post_id) or f"https://www.threads.net/t/{post_id}"
            log(f"Threads 발행 성공: {post_url}", "ok")
            return PostResult(success=True, url=post_url, post_id=post_id)
        except Exception as e:
            log(f"Threads 발행 예외: {e}", "error")
            return PostResult(success=False, message=str(e))

    def _fetch_permalink(self, post_id: str) -> str:
        """발행된 글의 정식 permalink 조회 (Threads Graph API)."""
        if blocked_reason():
            return ""
        try:
            r = requests.get(
                f"{GRAPH_BASE}/{post_id}",
                params={"fields": "permalink", "access_token": self.access_token},
                timeout=10,
            )
            if r.ok:
                permalink = r.json().get("permalink", "")
                if permalink:
                    return permalink
            else:
                observe_error(r.text)
                log(f"Threads permalink 조회 실패 [{r.status_code}]: {r.text[:200]}", "warn")
        except Exception as e:
            log(f"Threads permalink 조회 예외 (무시): {e}", "warn")
        return ""

    # ------------------------------------------------------------------
    # 토큰 갱신 (장기 토큰은 60일 유효, 매달 갱신 필요)
    # ------------------------------------------------------------------

    def refresh_token(self) -> Optional[str]:
        """장기 액세스 토큰 갱신 후 .env 자동 저장.

        common.threads_token 모듈에 위임.
        Returns:
            새 토큰 문자열, 실패 시 None
        """
        from common.threads_token import refresh_long_lived_token
        new_token = refresh_long_lived_token(save=True)
        if new_token:
            self.access_token = new_token
            log(f"Threads 토큰 갱신 완료 (.env 저장됨)", "ok")
        return new_token

    def get_profile(self) -> dict:
        """내 프로필 조회 (연결 테스트용).

        Returns:
            {'id': ..., 'name': ..., 'threads_profile_picture_url': ..., ...}
        """
        if blocked_reason():
            return {}
        url = f"{GRAPH_BASE}/me"
        params = {
            "fields":       "id,name,threads_profile_picture_url,threads_biography",
            "access_token": self.access_token,
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.ok:
                data = resp.json()
                log(f"Threads 프로필: {data.get('name')} (id={data.get('id')})", "ok")
                return data
            observe_error(resp.text)
            log(f"Threads 프로필 조회 실패: {resp.text[:200]}", "error")
            return {}
        except Exception as e:
            log(f"Threads 프로필 조회 예외: {e}", "error")
            return {}
