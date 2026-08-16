from __future__ import annotations

import os

import requests


def supabase_url() -> str:
    return os.getenv("SUPABASE_URL", "").strip().rstrip("/")


def supabase_key() -> str:
    return (os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")).strip()


def auth_ready() -> bool:
    return bool(supabase_url() and supabase_key())


def _auth_request(path: str, payload: dict, access_token: str = "") -> dict:
    key = supabase_key()
    headers = {"apikey": key, "Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    response = requests.post(
        f"{supabase_url()}/auth/v1/{path}", json=payload, headers=headers, timeout=30
    )
    if not response.ok:
        try:
            detail = response.json().get("msg") or response.json().get("error_description")
        except ValueError:
            detail = ""
        raise ValueError(detail or "Supabase Authの認証に失敗しました。")
    return response.json() if response.content else {}


def _session(result: dict, email: str = "") -> dict:
    user = result.get("user") or {}
    if not result.get("access_token") or not result.get("refresh_token") or not user.get("id"):
        raise ValueError("ログインセッションを取得できませんでした。")
    return {
        "access_token": result["access_token"],
        "refresh_token": result["refresh_token"],
        "expires_at": int(result.get("expires_at") or 0),
        "user_id": str(user["id"]),
        "email": user.get("email") or email,
    }


def sign_in(email: str, password: str) -> dict:
    result = _auth_request("token?grant_type=password", {"email": email, "password": password})
    return _session(result, email)


def sign_up(email: str, password: str) -> bool:
    result = _auth_request("signup", {"email": email, "password": password})
    return bool(result.get("access_token"))


def refresh_session(auth: dict) -> dict:
    result = _auth_request(
        "token?grant_type=refresh_token", {"refresh_token": auth.get("refresh_token", "")}
    )
    return _session(result, auth.get("email", ""))


def sign_out(auth: dict) -> None:
    try:
        _auth_request("logout", {}, auth.get("access_token", ""))
    except (requests.RequestException, ValueError):
        # 通信不能でもブラウザ側セッションは呼び出し元で必ず破棄する。
        pass
