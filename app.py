from __future__ import annotations

import importlib
import os
import time
import unicodedata

import pandas as pd
import streamlit as st

# Streamlit Cloudのホット更新でapp.pyだけが再実行されても、関連モジュールを
# GitHub上の最新版へ揃える。複数ファイル更新時の古いimportキャッシュを防ぐ。
from media_app import models as _models
from media_app import auth as _auth

_models = importlib.reload(_models)
_auth = importlib.reload(_auth)
from media_app import recognition as _recognition
from media_app import storage as _storage

_recognition = importlib.reload(_recognition)
_storage = importlib.reload(_storage)

from media_app.models import AlbumDraft, GENRES, MEDIA_TYPES, ORIGINS, split_people
from media_app.auth import auth_ready, refresh_session, sign_in, sign_out, sign_up
from media_app.recognition import (
    api_key,
    barcode_from_image,
    barcode_is_valid,
    enrich_album_metadata,
    extract_album_package_with_ai,
    extract_album_with_ai,
    lookup_cover_art,
    lookup_musicbrainz,
    lookup_musicbrainz_tracks,
    normalize_barcode,
)
from media_app.storage import (
    add_album,
    database_path,
    delete_album,
    duplicate_candidates,
    export_csv,
    export_tracks_csv,
    import_csv,
    initialize,
    get_album,
    list_tracks,
    list_all_tracks,
    list_albums,
    replace_tracks,
    set_auth_session,
    storage_description,
    StorageUnavailableError,
    using_supabase,
    update_album,
    update_album_cover,
    update_track_ratings,
)


st.set_page_config(page_title="わたしの音盤棚", page_icon="💿", layout="wide")
try:
    for secret_name in ("OPENAI_API_KEY", "OPENAI_VISION_MODEL", "DISCOGS_USER_TOKEN", "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY"):
        secret_value = st.secrets.get(secret_name, "")
        if secret_value and not os.getenv(secret_name):
            os.environ[secret_name] = str(secret_value)
except FileNotFoundError:
    pass


def auth_page() -> None:
    st.title("💿 わたしの音盤棚")
    st.caption("登録した音盤は、ログインしたご本人だけが閲覧できます。")
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        login_tab, signup_tab = st.tabs(["ログイン", "新規登録"])
        with login_tab:
            with st.form("login_form"):
                email = st.text_input("メールアドレス")
                password = st.text_input("パスワード", type="password")
                submitted = st.form_submit_button("ログイン", type="primary", width="stretch")
            if submitted:
                try:
                    st.session_state.auth = sign_in(email.strip(), password)
                    st.rerun()
                except Exception as exc:
                    st.error(f"ログインできませんでした: {exc}")
        with signup_tab:
            st.caption("初回のみアカウントを作成します。パスワードは8文字以上にしてください。")
            with st.form("signup_form"):
                new_email = st.text_input("メールアドレス", key="signup_email")
                new_password = st.text_input("パスワード", type="password", key="signup_password")
                confirmation = st.text_input("パスワード（確認）", type="password")
                created = st.form_submit_button("アカウントを作成", width="stretch")
            if created:
                if len(new_password) < 8:
                    st.error("パスワードは8文字以上にしてください。")
                elif new_password != confirmation:
                    st.error("確認用パスワードが一致しません。")
                else:
                    try:
                        active = sign_up(new_email.strip(), new_password)
                        if active:
                            st.success("登録しました。ログインしてください。")
                        else:
                            st.success("確認メールを送信しました。メール内のリンクを開いてからログインしてください。")
                    except Exception as exc:
                        st.error(f"登録できませんでした: {exc}")


supabase_configured = bool(os.getenv("SUPABASE_URL", "").strip())
if supabase_configured and not auth_ready():
    st.error("Supabase Authの公開キーが設定されていません。")
    st.warning("Streamlit CloudのSecretsへSUPABASE_KEY（Publishable keyまたはanon key）を設定してください。")
    st.stop()

st.session_state.setdefault("auth", None)
if auth_ready():
    if not st.session_state.auth:
        auth_page()
        st.stop()
    if int(st.session_state.auth.get("expires_at") or 0) <= int(time.time()) + 60:
        try:
            st.session_state.auth = refresh_session(st.session_state.auth)
        except Exception:
            st.session_state.auth = None
            st.warning("ログインの有効期限が切れました。もう一度ログインしてください。")
            st.rerun()
    set_auth_session(st.session_state.auth)

try:
    initialize()
except StorageUnavailableError as exc:
    st.error("Supabaseへ接続できません。")
    st.warning("Streamlit CloudのSecretsと、Supabaseのalbums・tracksテーブルを確認してください。")
    st.caption(str(exc))
    st.stop()

if "draft" not in st.session_state:
    st.session_state.draft = AlbumDraft()
if "draft_source" not in st.session_state:
    st.session_state.draft_source = "manual"
if "draft_tracks" not in st.session_state:
    st.session_state.draft_tracks = []
if st.session_state.pop("reset_new_form", False):
    st.session_state.draft = AlbumDraft()
    st.session_state.draft_source = "manual"
    for state_key in list(st.session_state):
        if state_key.startswith("new_"):
            del st.session_state[state_key]


def set_draft(album: AlbumDraft, source: str) -> None:
    st.session_state.draft = album
    st.session_state.draft_source = source
    # value= だけでは既存widgetの状態が優先されるため、各欄へ明示的に同期する。
    widget_values = {
        "new_title": album.title,
        "new_title_original": album.title_original,
        "new_artists": " ; ".join(album.artists),
        "new_composers": " ; ".join(album.composers),
        "new_performers": " ; ".join(album.performers),
        "new_label": album.label,
        "new_catalog": album.catalog_number,
        "new_barcode": album.barcode,
        "new_media": album.media_type,
        "new_discs": album.disc_count,
        "new_origin": album.origin,
        "new_country": album.country,
        "new_year": album.release_year,
        "new_genre": album.genre if album.genre in GENRES else "",
        "new_format": album.recording_format,
        "new_location": album.location,
        "new_purchase_date": album.purchase_date,
        "new_price": album.purchase_price or 0,
        "new_rating": album.rating,
        "new_condition": album.condition,
        "new_notes": album.notes,
    }
    st.session_state.update(widget_values)


def album_from_row(row: dict) -> AlbumDraft:
    values = {key: row.get(key) for key in AlbumDraft.model_fields}
    for field in ("artists", "composers", "performers"):
        values[field] = split_people(str(values.get(field) or ""))
    values["disc_count"] = int(values.get("disc_count") or 1)
    values["purchase_price"] = values.get("purchase_price") or None
    values["rating"] = int(values.get("rating") or 0)
    return AlbumDraft(**values)


def album_fields(prefix: str, draft: AlbumDraft) -> dict:
    title = st.text_input("アルバム名 *", value=draft.title, key=f"{prefix}_title")
    title_original = st.text_input("原題・別タイトル", value=draft.title_original, key=f"{prefix}_title_original")
    artists = st.text_input("主要アーティスト／演奏団体", value=" ; ".join(draft.artists), help="複数は ; で区切ります", key=f"{prefix}_artists")
    composers = st.text_input("作曲家", value=" ; ".join(draft.composers), key=f"{prefix}_composers")
    performers = st.text_input("指揮者・独奏者・その他演奏者", value=" ; ".join(draft.performers), key=f"{prefix}_performers")
    c1, c2, c3 = st.columns(3)
    label = c1.text_input("レーベル", value=draft.label, key=f"{prefix}_label")
    catalog_number = c2.text_input("規格品番", value=draft.catalog_number, key=f"{prefix}_catalog")
    barcode = c3.text_input("バーコード", value=draft.barcode, key=f"{prefix}_barcode")
    c4, c5, c6 = st.columns(3)
    media_index = MEDIA_TYPES.index(draft.media_type) if draft.media_type in MEDIA_TYPES else 0
    media_type = c4.selectbox("盤種", MEDIA_TYPES, index=media_index, key=f"{prefix}_media")
    disc_count = c5.number_input("枚数", min_value=1, value=draft.disc_count, step=1, key=f"{prefix}_discs")
    origin_index = ORIGINS.index(draft.origin) if draft.origin in ORIGINS else 2
    origin = c6.selectbox("国内盤／輸入盤", ORIGINS, index=origin_index, key=f"{prefix}_origin")
    c7, c8, c9 = st.columns(3)
    country = c7.text_input("発売国", value=draft.country, key=f"{prefix}_country")
    release_year = c8.text_input("発売年", value=draft.release_year, key=f"{prefix}_year")
    genre_options = [""] + GENRES
    genre_index = genre_options.index(draft.genre) if draft.genre in genre_options else 0
    genre = c9.selectbox("ジャンル", genre_options, index=genre_index, key=f"{prefix}_genre")
    c10, c11 = st.columns(2)
    recording_format = c10.text_input("録音方式", value=draft.recording_format, placeholder="例: Stereo / DSD / 5.1ch", key=f"{prefix}_format")
    location = c11.text_input("保管場所", value=draft.location, placeholder="例: 棚A-3", key=f"{prefix}_location")
    c12, c13, c14 = st.columns(3)
    purchase_date = c12.text_input("購入日", value=draft.purchase_date, placeholder="YYYY-MM-DD", key=f"{prefix}_purchase_date")
    purchase_price = c13.number_input("購入価格（円）", min_value=0, value=draft.purchase_price or 0, step=100, key=f"{prefix}_price")
    condition = c14.text_input("状態", value=draft.condition, placeholder="新品／中古／帯あり等", key=f"{prefix}_condition")
    rating_labels = ["未評価", "★", "★★", "★★★", "★★★★", "★★★★★"]
    rating = st.selectbox(
        "お気に入り度",
        options=range(6),
        index=int(draft.rating),
        format_func=lambda value: rating_labels[value],
        key=f"{prefix}_rating",
    )
    notes = st.text_area("メモ", value=draft.notes, key=f"{prefix}_notes")
    return dict(
        title=title, title_original=title_original, artists=split_people(artists),
        composers=split_people(composers), performers=split_people(performers), label=label,
        catalog_number=catalog_number, barcode=normalize_barcode(barcode), media_type=media_type,
        disc_count=int(disc_count), origin=origin, country=country, release_year=release_year,
        genre=genre, recording_format=recording_format, location=location,
        purchase_date=purchase_date, purchase_price=int(purchase_price) or None, rating=int(rating),
        condition=condition, notes=notes,
        musicbrainz_release_id=draft.musicbrainz_release_id,
        cover_url=draft.cover_url,
        cover_source=draft.cover_source,
    )


st.title("💿 わたしの音盤棚")
st.caption("CD・SACDを、見つけやすく、重複なく。国内盤も輸入盤もまとめて管理。")
st.caption("アプリ版: 0.7.0（Supabase Authログイン対応版）")
if auth_ready():
    account, logout = st.columns([5, 1])
    account.caption(f"ログイン中: {st.session_state.auth.get('email', '')}")
    if logout.button("ログアウト", width="stretch"):
        sign_out(st.session_state.auth)
        st.session_state.auth = None
        set_auth_session(None)
        st.rerun()


def duration_text(milliseconds) -> str:
    if not milliseconds:
        return ""
    total_seconds = round(int(milliseconds) / 1000)
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def normalize_search_text(value: str) -> str:
    """全半角と空白を揃え、検索語の外側の引用符を除く。"""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = " ".join(normalized.split())
    return normalized.strip(" \"'“”‘’")


album_param = st.query_params.get("album")
if album_param:
    try:
        detail_album = get_album(int(album_param))
    except (TypeError, ValueError):
        detail_album = None
    if not detail_album:
        st.error("指定されたアルバムが見つかりません。")
        st.stop()
    st.header(detail_album["title"])
    st.caption(" / ".join(filter(None, [detail_album.get("artists", ""), detail_album.get("label", ""), detail_album.get("catalog_number", "")])))
    if int(detail_album.get("rating") or 0):
        st.markdown(f"お気に入り度: {'★' * int(detail_album['rating'])}{'☆' * (5 - int(detail_album['rating']))}")
    if detail_album.get("cover_url"):
        st.image(detail_album["cover_url"], width=420, caption=detail_album.get("cover_source") or "アルバムジャケット")
    elif detail_album.get("musicbrainz_release_id") or detail_album.get("barcode"):
        if st.button("ジャケット画像を取得"):
            try:
                release_id = detail_album.get("musicbrainz_release_id", "")
                if not release_id and detail_album.get("barcode"):
                    matched_cover_album = lookup_musicbrainz(detail_album["barcode"])
                    release_id = matched_cover_album.musicbrainz_release_id if matched_cover_album else ""
                cover_url = lookup_cover_art(release_id)
                if cover_url:
                    update_album_cover(int(album_param), cover_url, "Cover Art Archive")
                    st.rerun()
                else:
                    st.warning("Cover Art Archiveにこの盤のジャケット画像がありませんでした。")
            except Exception as exc:
                st.error(f"ジャケット画像の取得に失敗しました: {exc}")
    detail_tracks = list_tracks(int(album_param))
    st.subheader("情報の補完")
    st.caption("保存済みの入力内容を残したまま、空欄・ジャケット・未登録の収録曲を検索します。")
    if st.button("アルバム情報・収録曲を再取得して補完", type="primary"):
        try:
            with st.spinner("MusicBrainz、Discogs、Webを順に検索中…"):
                enriched = enrich_album_metadata(album_from_row(detail_album))
                update_album(int(album_param), enriched.album)
                if enriched.tracks and not detail_tracks:
                    replace_tracks(int(album_param), enriched.tracks)
            if enriched.source:
                st.success(f"{enriched.source}から情報を補完しました。")
            else:
                st.warning("一致を確認できる追加情報は見つかりませんでした。")
            st.rerun()
        except Exception as exc:
            st.error(f"情報の再取得に失敗しました: {exc}")
    if detail_tracks:
        track_rating_options = ["未評価", "★", "★★", "★★★", "★★★★", "★★★★★"]
        track_display = pd.DataFrame([
            {
                "ID": row["id"],
                "Disc": row["disc_number"], "No.": row["track_number"], "曲名": row["title"],
                "お気に入り度": track_rating_options[int(row.get("rating") or 0)],
                "アーティスト": row["artists"], "演者": row["performers"],
                "作曲者等": row["composers"], "時間": duration_text(row["duration_ms"]),
                "ISRC": row["isrc"],
            }
            for row in detail_tracks
        ])
        st.caption("各曲のお気に入り度を選び、一覧の下にある保存ボタンを押してください。")
        edited_tracks = st.data_editor(
            track_display,
            hide_index=True,
            width="stretch",
            disabled=[column for column in track_display.columns if column != "お気に入り度"],
            column_config={
                "ID": None,
                "お気に入り度": st.column_config.SelectboxColumn(
                    "お気に入り度", options=track_rating_options, required=True, width="medium"
                ),
            },
            key=f"track_rating_editor_{album_param}",
        )
        if st.button("収録曲のお気に入り度を保存", type="primary"):
            original_ratings = {int(row["id"]): int(row.get("rating") or 0) for row in detail_tracks}
            changed_ratings = {}
            for _, edited_row in edited_tracks.iterrows():
                track_id = int(edited_row["ID"])
                rating = track_rating_options.index(edited_row["お気に入り度"])
                if rating != original_ratings[track_id]:
                    changed_ratings[track_id] = rating
            if changed_ratings:
                update_track_ratings(changed_ratings)
                st.success(f"{len(changed_ratings)}曲のお気に入り度を更新しました。")
                st.rerun()
            else:
                st.info("お気に入り度の変更はありません。")
        st.metric("収録曲数", len(detail_tracks))
        album_track_rows = [dict(row, album_id=detail_album["id"], album_title=detail_album["title"], album_artists=detail_album.get("artists", ""), label=detail_album.get("label", ""), catalog_number=detail_album.get("catalog_number", ""), barcode=detail_album.get("barcode", ""), media_type=detail_album.get("media_type", ""), release_year=detail_album.get("release_year", ""), track_title=row["title"], track_artists=row["artists"]) for row in detail_tracks]
        st.download_button(
            "このアルバムの収録曲CSVを書き出す",
            export_tracks_csv(album_track_rows).encode("utf-8-sig"),
            file_name=f"album_{detail_album['id']}_tracks.csv",
            mime="text/csv",
        )
    else:
        st.info("このアルバムの収録曲情報はまだありません。")
        if detail_album.get("barcode") and st.button("バーコードから収録曲を取得", type="primary"):
            try:
                with st.spinner("MusicBrainzから収録曲を取得中…"):
                    matched = lookup_musicbrainz(detail_album["barcode"])
                    fetched_tracks = lookup_musicbrainz_tracks(
                        matched.musicbrainz_release_id if matched else ""
                    )
                if fetched_tracks:
                    replace_tracks(int(album_param), fetched_tracks)
                    st.success(f"{len(fetched_tracks)}曲を保存しました。")
                    st.rerun()
                else:
                    st.warning("MusicBrainzにこの盤の収録曲情報がありませんでした。")
            except Exception as exc:
                st.error(f"収録曲の取得に失敗しました: {exc}")
    st.link_button("音盤棚へ戻る", st.context.url)
    st.stop()

tab_add, tab_shelf, tab_track_search, tab_favorites, tab_import, tab_settings = st.tabs(
    ["音盤を登録", "音盤棚", "楽曲検索", "お気に入り曲", "一括登録", "設定"]
)

with tab_add:
    left, right = st.columns([0.8, 1.6], gap="large")
    with left:
        st.subheader("1. 情報を取得")
        mode = st.radio(
            "読み取り方法",
            ["バーコード写真", "ジャケット画像（AI）", "番号を入力", "手入力"],
        )
        recognition = st.session_state.get("last_recognition")
        if recognition:
            if recognition["status"] == "success":
                st.success(recognition["message"])
            else:
                st.warning(recognition["message"])
        selected_files = []
        if mode in {"バーコード写真", "ジャケット画像（AI）"}:
            st.info("下のボタンを押したときだけ、カメラまたは写真ライブラリが開きます。")
            uploaded = st.file_uploader(
                "📷 撮影・写真を選択",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=mode == "ジャケット画像（AI）",
                help=(
                    "iPhoneでは『写真またはビデオを撮る』を選べます。"
                    "AI読取では表・裏・帯・盤面を最大4枚程度選ぶと精度が上がります。"
                ),
                key=f"upload_{mode}",
            )
            if uploaded:
                selected_files.extend(uploaded if isinstance(uploaded, list) else [uploaded])
            if selected_files:
                st.image([item.getvalue() for item in selected_files], width=180)

        if mode == "バーコード写真" and st.button("写真からバーコードを読み取る", type="primary", width="stretch"):
            if not selected_files:
                st.warning("バーコードが大きく写った写真を撮影または選択してください。")
            else:
                try:
                    barcode = barcode_from_image(selected_files[0].getvalue())
                    if not barcode:
                        st.warning("バーコードを検出できませんでした。明るい場所で近づいて再撮影してください。")
                    else:
                        # 外部検索に失敗しても、読み取れたバーコードは必ずフォームへ残す。
                        found = None
                        lookup_error = ""
                        try:
                            with st.spinner("バーコードと音盤情報を確認中…"):
                                enriched = enrich_album_metadata(AlbumDraft(barcode=barcode))
                                found = enriched.album if enriched.album.title else None
                                st.session_state.draft_tracks = enriched.tracks
                        except Exception as exc:
                            lookup_error = str(exc)
                        set_draft(found or AlbumDraft(barcode=barcode), "barcode-photo")
                        if found:
                            st.session_state.last_recognition = {
                                "status": "success",
                                "message": f"バーコード {barcode} を読み取り、『{found.title}』の情報を右側へ反映しました。",
                            }
                        elif lookup_error:
                            st.session_state.last_recognition = {
                                "status": "warning",
                                "message": f"バーコード {barcode} は読み取れました。外部検索に接続できないため、番号だけを右側へ反映しました。",
                            }
                        else:
                            st.session_state.last_recognition = {
                                "status": "warning",
                                "message": f"バーコード {barcode} は読み取れましたが、複数の検索先で一致する盤を確認できませんでした。番号を右側へ反映しました。",
                            }
                        st.rerun()
                except Exception as exc:
                    st.error(f"読み取りに失敗しました: {exc}")

        if mode == "ジャケット画像（AI）":
            if not api_key():
                st.warning("AI読取には設定画面の案内に従ってOPENAI_API_KEYを設定してください。")
            if st.button("AIで音盤情報を読み取る", type="primary", width="stretch", disabled=not api_key()):
                if not selected_files:
                    st.warning("写真を1枚以上撮影または選択してください。")
                else:
                    try:
                        images = [(item.getvalue(), getattr(item, "type", "image/jpeg")) for item in selected_files[:4]]
                        with st.spinner("ジャケット・帯・盤面を分析中…"):
                            image_result = extract_album_package_with_ai(images)
                            enriched = enrich_album_metadata(image_result.album, image_result.tracks)
                            found = enriched.album
                            st.session_state.draft_tracks = enriched.tracks
                        set_draft(found, enriched.source or "AI画像")
                        st.success(f"画像を読み取り、{enriched.source or '画像内'}の情報を反映しました。右側で必ず確認してください。")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"AI読取に失敗しました: {exc}")

        barcode_input = ""
        if mode == "番号を入力":
            barcode_input = st.text_input("JAN／UPC／EANバーコード", placeholder="数字を入力または貼り付け")
        if mode == "番号を入力" and st.button("MusicBrainzから検索", type="primary", width="stretch"):
            barcode = normalize_barcode(barcode_input)
            if not barcode_is_valid(barcode):
                st.error("バーコードの桁数またはチェックディジットが正しくありません。")
            else:
                try:
                    with st.spinner("音盤情報を検索中…"):
                        enriched = enrich_album_metadata(AlbumDraft(barcode=barcode))
                    if enriched.album.title:
                        st.session_state.draft_tracks = enriched.tracks
                        set_draft(enriched.album, enriched.source)
                        st.success("候補が見つかりました。右側で確認してください。")
                        st.rerun()
                    else:
                        set_draft(AlbumDraft(barcode=barcode), "barcode")
                        st.warning("該当データがありません。右側で補ってください。")
                except Exception as exc:
                    st.error(f"検索に失敗しました: {exc}")
        if mode == "手入力":
            st.info("右側へ直接入力してください。規格品番のない古い盤やボックス物も登録できます。")
    with right:
        st.subheader("2. 内容を確認して保存")
        with st.form("album_form"):
            values = album_fields("new", st.session_state.draft)
            saved = st.form_submit_button("音盤棚に保存", type="primary", width="stretch")
        if saved:
            album = AlbumDraft(**values)
            if not album.title.strip():
                st.error("アルバム名は必須です。")
            else:
                duplicates = duplicate_candidates(album.barcode, album.catalog_number)
                if duplicates:
                    st.warning(f"同じバーコードまたは規格品番の登録が {len(duplicates)} 件あります。内容を確認してください。")
                else:
                    saved_album_id = add_album(album, st.session_state.draft_source)
                    if st.session_state.draft_tracks:
                        replace_tracks(saved_album_id, st.session_state.draft_tracks)
                    st.session_state.reset_new_form = True
                    st.session_state.draft_tracks = []
                    st.session_state.pop("last_recognition", None)
                    st.success("音盤棚に保存しました。")
                    st.rerun()

with tab_shelf:
    query = st.text_input("音盤棚を検索", placeholder="アルバム、演奏者、作曲家、レーベル、規格品番、保管場所…")
    rows = list_albums(query)
    f1, f2, f3 = st.columns(3)
    media_filter = f1.multiselect("盤種", MEDIA_TYPES)
    origin_filter = f2.multiselect("国内／輸入", ORIGINS)
    genre_filter = f3.multiselect("ジャンル", GENRES)
    filtered = [row for row in rows if (not media_filter or row["media_type"] in media_filter) and (not origin_filter or row["origin"] in origin_filter) and (not genre_filter or row["genre"] in genre_filter)]
    m1, m2, m3 = st.columns(3)
    m1.metric("登録タイトル", len(filtered))
    m2.metric("総ディスク枚数", sum(int(row.get("disc_count") or 1) for row in filtered))
    m3.metric("SACD系", sum("SACD" in row.get("media_type", "") for row in filtered))
    if filtered:
        display_rows = [dict(row, track_link=f"{st.context.url}?album={row['id']}") for row in filtered]
        display = pd.DataFrame(display_rows).rename(columns={
            "id": "ID", "title": "アルバム", "artists": "アーティスト", "composers": "作曲家",
            "performers": "演奏者", "label": "レーベル", "catalog_number": "規格品番",
            "barcode": "バーコード", "media_type": "盤種", "disc_count": "枚数", "origin": "国内／輸入",
            "country": "発売国", "release_year": "発売年", "genre": "ジャンル", "location": "保管場所",
            "rating": "お気に入り度",
            "track_link": "収録曲",
            "cover_url": "ジャケット",
        })
        rating_options = ["未評価", "★", "★★", "★★★", "★★★★", "★★★★★"]
        genre_options = ["未設定"] + GENRES
        if "お気に入り度" in display:
            display["お気に入り度"] = display["お気に入り度"].apply(
                lambda value: rating_options[int(value or 0)]
            )
        if "ジャンル" in display:
            display["ジャンル"] = display["ジャンル"].apply(
                lambda value: value if value in GENRES else "未設定"
            )
        preferred = ["ID", "ジャケット", "アルバム", "お気に入り度", "収録曲", "アーティスト", "作曲家", "演奏者", "レーベル", "規格品番", "盤種", "枚数", "国内／輸入", "発売国", "発売年", "ジャンル", "保管場所"]
        shelf_columns = [column for column in preferred if column in display]
        st.caption("お気に入り度とジャンルのセルは、一覧のまま選択・編集できます。")
        edited_shelf = st.data_editor(
            display[shelf_columns],
            hide_index=True,
            width="stretch",
            disabled=[column for column in shelf_columns if column not in {"お気に入り度", "ジャンル"}],
            column_config={
                "収録曲": st.column_config.LinkColumn("収録曲", display_text="別タブで見る"),
                "ジャケット": st.column_config.ImageColumn("ジャケット", width="small"),
                "お気に入り度": st.column_config.SelectboxColumn(
                    "お気に入り度", options=rating_options, required=True, width="medium"
                ),
                "ジャンル": st.column_config.SelectboxColumn(
                    "ジャンル", options=genre_options, required=True, width="medium"
                ),
            },
            key="shelf_rating_editor",
        )
        if st.button("一覧の変更を保存", type="primary"):
            rows_by_id = {int(row["id"]): row for row in filtered}
            changed = 0
            for _, edited_row in edited_shelf.iterrows():
                album_id = int(edited_row["ID"])
                selected_rating = rating_options.index(edited_row["お気に入り度"])
                original = rows_by_id[album_id]
                original_genre_label = original.get("genre") if original.get("genre") in GENRES else "未設定"
                selected_genre_label = edited_row["ジャンル"]
                rating_changed = selected_rating != int(original.get("rating") or 0)
                genre_changed = selected_genre_label != original_genre_label
                if rating_changed or genre_changed:
                    album = album_from_row(original)
                    if rating_changed:
                        album.rating = selected_rating
                    if genre_changed:
                        album.genre = "" if selected_genre_label == "未設定" else selected_genre_label
                    update_album(album_id, album)
                    changed += 1
            if changed:
                st.success(f"{changed}件の一覧情報を更新しました。")
                st.rerun()
            else:
                st.info("お気に入り度・ジャンルの変更はありません。")
        st.download_button("CSVを書き出す", export_csv(filtered).encode("utf-8-sig"), "cd_sacd_collection.csv", "text/csv")
        all_track_rows = list_all_tracks()
        if all_track_rows:
            st.download_button(
                f"全収録曲CSVを書き出す（{len(all_track_rows):,}曲）",
                export_tracks_csv(all_track_rows).encode("utf-8-sig"),
                "cd_sacd_all_tracks.csv",
                "text/csv",
            )
        choices = {f"{row['title']}（ID: {row['id']}）": row for row in filtered}
        with st.expander("登録内容を編集"):
            selected_label = st.selectbox("編集する音盤", choices, key="edit_choice")
            selected = choices[selected_label]
            with st.form(f"edit_form_{selected['id']}"):
                edited_values = album_fields(f"edit_{selected['id']}", album_from_row(selected))
                update_clicked = st.form_submit_button("変更を保存", type="primary")
            if update_clicked:
                update_album(selected["id"], AlbumDraft(**edited_values))
                st.success("更新しました。")
                st.rerun()
        with st.expander("登録を削除"):
            delete_label = st.selectbox("削除する音盤", choices, key="delete_choice")
            confirm = st.checkbox("削除することを確認しました")
            if st.button("この登録を削除", disabled=not confirm):
                delete_album(choices[delete_label]["id"])
                st.success("削除しました。")
                st.rerun()
    else:
        st.info("条件に合う音盤はありません。")

with tab_track_search:
    st.subheader("全アルバムの収録曲を検索")
    st.caption("曲名、アルバム、アーティスト、演者、作曲者、レーベル、規格品番、ISRCを横断検索します。")
    track_query = st.text_input(
        "検索語",
        placeholder="例: Mozart、ピアノ、Blue Note、ABC-123",
        key="global_track_query",
        help="入力後にEnterを1回押すと、表示中の検索語ですぐに検索します。",
    ).strip()
    track_search_mode = st.radio(
        "検索方法",
        ["フレーズ一致", "すべての語を含む"],
        horizontal=True,
        help=(
            "フレーズ一致は『I feel』のような語順のまま、1つの項目内で探します。"
            "「すべての語を含む」は、語が曲名・演者など別々の項目にあっても一致します。"
        ),
        key="global_track_search_mode",
    )
    if track_query:
        with st.spinner("全収録曲を検索中…"):
            searchable_tracks = list_all_tracks()
        normalized_query = normalize_search_text(track_query)
        terms = [term for term in normalized_query.split() if term]
        search_fields = (
            "track_title", "track_artists", "performers", "composers", "album_title",
            "album_artists", "label", "catalog_number", "barcode", "isrc",
        )
        if track_search_mode == "フレーズ一致":
            matched_tracks = [
                row for row in searchable_tracks
                if any(
                    normalized_query in normalize_search_text(row.get(field, ""))
                    for field in search_fields
                )
            ]
            st.caption(f"検索条件: 「{normalized_query}」が同じ項目内に連続して含まれる曲")
        else:
            matched_tracks = [
                row for row in searchable_tracks
                if all(
                    any(term in normalize_search_text(row.get(field, "")) for field in search_fields)
                    for term in terms
                )
            ]
            st.caption("検索条件: 空白で区切ったすべての語を、全項目から検索")
        st.metric("検索結果", f"{len(matched_tracks):,}曲")
        if matched_tracks:
            result_display = pd.DataFrame([
                {
                    "お気に入り度": "★" * int(row.get("rating") or 0),
                    "アルバム": row.get("album_title", ""),
                    "収録曲": f"{st.context.url}?album={row.get('album_id')}",
                    "Disc": row.get("disc_number", 1),
                    "No.": row.get("track_number", ""),
                    "曲名": row.get("track_title", ""),
                    "アーティスト": row.get("track_artists", ""),
                    "演者": row.get("performers", ""),
                    "作曲者等": row.get("composers", ""),
                    "時間": duration_text(row.get("duration_ms")),
                    "レーベル": row.get("label", ""),
                    "規格品番": row.get("catalog_number", ""),
                    "ISRC": row.get("isrc", ""),
                }
                for row in matched_tracks
            ])
            st.dataframe(
                result_display,
                hide_index=True,
                width="stretch",
                column_config={
                    "収録曲": st.column_config.LinkColumn("アルバム詳細", display_text="開く"),
                },
            )
            st.download_button(
                f"検索結果CSVを書き出す（{len(matched_tracks):,}曲）",
                export_tracks_csv(matched_tracks).encode("utf-8-sig"),
                file_name="track_search_results.csv",
                mime="text/csv",
            )
        else:
            st.info("検索語に一致する曲はありません。表記を短くして再検索してください。")
    else:
        st.info("検索語を入力して「全楽曲から検索」を押してください。")

with tab_favorites:
    st.subheader("お気に入り曲からプレイリスト候補を作る")
    track_rating_labels = {1: "★", 2: "★★", 3: "★★★", 4: "★★★★", 5: "★★★★★"}
    selected_track_ratings = st.multiselect(
        "抽出するお気に入り度",
        options=list(track_rating_labels),
        default=[4, 5],
        format_func=lambda value: track_rating_labels[value],
        help="複数の星を同時に選択できます。未評価の曲は抽出対象外です。",
    )
    all_favorite_candidates = list_all_tracks()
    favorite_tracks = [
        row for row in all_favorite_candidates
        if int(row.get("rating") or 0) in selected_track_ratings
    ]
    st.metric("抽出した楽曲", f"{len(favorite_tracks):,}曲")
    if favorite_tracks:
        favorite_display = pd.DataFrame([
            {
                "お気に入り度": track_rating_labels[int(row.get("rating") or 0)],
                "アルバム": row.get("album_title", ""),
                "Disc": row.get("disc_number", 1),
                "No.": row.get("track_number", ""),
                "曲名": row.get("track_title", ""),
                "アーティスト": row.get("track_artists", ""),
                "演者": row.get("performers", ""),
                "作曲者等": row.get("composers", ""),
                "時間": duration_text(row.get("duration_ms")),
                "レーベル": row.get("label", ""),
            }
            for row in favorite_tracks
        ])
        st.dataframe(favorite_display, hide_index=True, width="stretch")
        st.download_button(
            f"プレイリスト候補CSVを書き出す（{len(favorite_tracks):,}曲）",
            export_tracks_csv(favorite_tracks).encode("utf-8-sig"),
            file_name="favorite_track_playlist_candidates.csv",
            mime="text/csv",
        )
    elif selected_track_ratings:
        st.info("選択したお気に入り度に該当する曲はありません。")
    else:
        st.info("抽出するお気に入り度を1つ以上選択してください。")

with tab_import:
    st.subheader("CSVから一括登録")
    st.caption("大量の既存データはCSVでまとめて登録できます。書き出したCSVと同じ英字列名を使用します。")
    uploaded_csv = st.file_uploader("CSVファイル", type=["csv"])
    if uploaded_csv and st.button("CSVを登録", type="primary"):
        added, errors = import_csv(uploaded_csv.getvalue())
        st.success(f"{added}件を登録しました。")
        if errors:
            st.warning("\n".join(errors[:20]))

with tab_settings:
    if auth_ready():
        st.subheader("ログイン")
        st.success(f"{st.session_state.auth.get('email', '')} でログインしています。")
    st.subheader("保存先")
    st.code(storage_description(), language=None)
    if using_supabase():
        st.success("Supabase PostgreSQLへ永続保存しています。")
    else:
        st.caption("環境変数 MEDIA_DB_PATH でiCloud Drive上のSQLiteファイルも指定できます。同じDBを複数プロセスから同時に開かないでください。")
    st.subheader("メタデータ")
    st.caption("MusicBrainzをバーコード、規格品番、タイトル＋アーティストの順で検索し、Discogs、AI Web検索へ補完します。")
    if os.getenv("DISCOGS_USER_TOKEN", "").strip():
        st.success("DISCOGS_USER_TOKEN は設定済みです（値は表示しません）。")
    else:
        st.info("Discogs検索は未設定です。任意でDISCOGS_USER_TOKENをSecretsへ追加すると検索範囲が広がります。")
    st.subheader("AI画像読取")
    if api_key():
        st.success("OPENAI_API_KEY は設定済みです（値は表示しません）。")
    else:
        st.warning("OPENAI_API_KEY は未設定です。ジャケット画像のAI読取を使う場合は設定してください。")
    st.code('OPENAI_API_KEY = "sk-..."\nOPENAI_VISION_MODEL = "gpt-5.6-luna"\nDISCOGS_USER_TOKEN = "..."', language="toml")
    st.caption("上記を .streamlit/secrets.toml に保存します。APIキーをソースやGitHubへ登録しないでください。")
