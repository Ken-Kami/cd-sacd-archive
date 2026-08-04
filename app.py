from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from media_app.models import AlbumDraft, GENRES, MEDIA_TYPES, ORIGINS, split_people
from media_app.recognition import (
    api_key,
    barcode_from_image,
    barcode_is_valid,
    extract_album_with_ai,
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
    import_csv,
    initialize,
    get_album,
    list_tracks,
    list_albums,
    replace_tracks,
    update_album,
)


st.set_page_config(page_title="わたしの音盤棚", page_icon="💿", layout="wide")
try:
    for secret_name in ("OPENAI_API_KEY", "OPENAI_VISION_MODEL"):
        secret_value = st.secrets.get(secret_name, "")
        if secret_value and not os.getenv(secret_name):
            os.environ[secret_name] = str(secret_value)
except FileNotFoundError:
    pass
initialize()

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
    notes = st.text_area("メモ", value=draft.notes, key=f"{prefix}_notes")
    return dict(
        title=title, title_original=title_original, artists=split_people(artists),
        composers=split_people(composers), performers=split_people(performers), label=label,
        catalog_number=catalog_number, barcode=normalize_barcode(barcode), media_type=media_type,
        disc_count=int(disc_count), origin=origin, country=country, release_year=release_year,
        genre=genre, recording_format=recording_format, location=location,
        purchase_date=purchase_date, purchase_price=int(purchase_price) or None,
        condition=condition, notes=notes,
        musicbrainz_release_id=draft.musicbrainz_release_id,
    )


st.title("💿 わたしの音盤棚")
st.caption("CD・SACDを、見つけやすく、重複なく。国内盤も輸入盤もまとめて管理。")
st.caption("アプリ版: 0.3.0（収録曲詳細版）")


def duration_text(milliseconds) -> str:
    if not milliseconds:
        return ""
    total_seconds = round(int(milliseconds) / 1000)
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


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
    detail_tracks = list_tracks(int(album_param))
    if detail_tracks:
        track_display = pd.DataFrame([
            {
                "Disc": row["disc_number"], "No.": row["track_number"], "曲名": row["title"],
                "アーティスト": row["artists"], "演者": row["performers"],
                "作曲者等": row["composers"], "時間": duration_text(row["duration_ms"]),
                "ISRC": row["isrc"],
            }
            for row in detail_tracks
        ])
        st.dataframe(track_display, hide_index=True, width="stretch")
        st.metric("収録曲数", len(detail_tracks))
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

tab_add, tab_shelf, tab_import, tab_settings = st.tabs(["音盤を登録", "音盤棚", "一括登録", "設定"])

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
                                found = lookup_musicbrainz(barcode)
                                if found:
                                    st.session_state.draft_tracks = lookup_musicbrainz_tracks(found.musicbrainz_release_id)
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
                                "message": f"バーコード {barcode} は読み取れましたが、MusicBrainzに該当情報がありません。番号を右側へ反映しました。",
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
                            found = extract_album_with_ai(images)
                            if found.barcode:
                                catalog = lookup_musicbrainz(found.barcode)
                                if catalog:
                                    found.musicbrainz_release_id = catalog.musicbrainz_release_id
                                    st.session_state.draft_tracks = lookup_musicbrainz_tracks(catalog.musicbrainz_release_id)
                                    for field in ("title", "artists", "label", "catalog_number", "country", "release_year"):
                                        if not getattr(found, field):
                                            setattr(found, field, getattr(catalog, field))
                        set_draft(found, "AI画像")
                        st.success("画像から情報を読み取りました。右側で必ず確認してください。")
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
                        found = lookup_musicbrainz(barcode)
                    if found:
                        st.session_state.draft_tracks = lookup_musicbrainz_tracks(found.musicbrainz_release_id)
                        set_draft(found, "MusicBrainz")
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
            "track_link": "収録曲",
        })
        preferred = ["ID", "アルバム", "収録曲", "アーティスト", "作曲家", "演奏者", "レーベル", "規格品番", "盤種", "枚数", "国内／輸入", "発売国", "発売年", "ジャンル", "保管場所"]
        st.dataframe(
            display[[column for column in preferred if column in display]],
            hide_index=True,
            width="stretch",
            column_config={
                "収録曲": st.column_config.LinkColumn("収録曲", display_text="別タブで見る"),
            },
        )
        st.download_button("CSVを書き出す", export_csv(filtered).encode("utf-8-sig"), "cd_sacd_collection.csv", "text/csv")
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
    st.subheader("保存先")
    st.code(str(database_path()), language=None)
    st.caption("環境変数 MEDIA_DB_PATH でiCloud Drive上のSQLiteファイルも指定できます。同じDBを複数プロセスから同時に開かないでください。")
    st.subheader("メタデータ")
    st.caption("バーコード検索にはMusicBrainzの公開Webサービスを使用します。取得結果は保存前に必ず確認・修正できます。")
    st.subheader("AI画像読取")
    if api_key():
        st.success("OPENAI_API_KEY は設定済みです（値は表示しません）。")
    else:
        st.warning("OPENAI_API_KEY は未設定です。ジャケット画像のAI読取を使う場合は設定してください。")
    st.code('OPENAI_API_KEY = "sk-..."\nOPENAI_VISION_MODEL = "gpt-5.6-luna"', language="toml")
    st.caption("上記を .streamlit/secrets.toml に保存します。APIキーをソースやGitHubへ登録しないでください。")
