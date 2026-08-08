# わたしの音盤棚

1000枚を超えるCD・SACDコレクションを、MacとiPhoneから管理するStreamlitアプリです。
叢書管理アプリ「わたしの本棚」の操作体系を引き継いでいます。

## 主な機能

- CD、SACD、SACD Hybrid、Blu-ray Audioを登録
- JAN／UPC／EANからMusicBrainzのメタデータを取得
- 「撮影・写真を選択」を押したときだけカメラを開き、JAN・UPC・EANを読み取り
- 表裏ジャケット・帯・盤面の複数画像からAIで音盤情報を抽出
- バーコード、規格品番、タイトル＋アーティストによる段階的検索
- 保存済みアルバムの空欄・ジャケット・収録曲を後から再取得
- 0〜5個の星によるお気に入り度の登録・一覧での一括編集
- 収録曲ごとの星評価とアルバム詳細での一括編集
- Cover Art Archiveから盤に対応する表ジャケットを取得・表示
- 国内盤／輸入盤、規格品番、レーベル、演奏者、作曲家、保管場所などを記録
- 全項目横断検索、盤種・出自・ジャンルで絞り込み
- バーコード／規格品番による重複候補の検出
- 登録後の全項目編集、削除、CSV入出力
- 総タイトル数、総ディスク枚数、SACD数を集計

## 起動

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

AI画像読取を使う場合は `.streamlit/secrets.toml`（Git管理対象外）へ設定します。

Discogsも検索する場合は、DiscogsのUser Tokenを任意で追加します。

```toml
DISCOGS_USER_TOKEN = "..."
```

```toml
OPENAI_API_KEY = "sk-..."
OPENAI_VISION_MODEL = "gpt-5.6-luna"
```

既定の保存先は `data/collection.db` です。iCloud Drive等を使う場合は次のように指定します。

```bash
export MEDIA_DB_PATH="/path/to/CDArchive/collection.db"
streamlit run app.py
```

同一SQLiteファイルを複数のMacや複数プロセスから同時に開かないでください。

## Streamlit Cloud / Supabase

Streamlit CloudではSQLiteを使わず、Supabase PostgreSQLへ永続保存します。

1. SupabaseのSQL Editorで `supabase_schema.sql` を実行します。
2. Streamlit Cloudの `App settings > Secrets` に次を設定します。

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "YOUR_SERVICE_ROLE_KEY"
```

`service_role`キーは管理者権限を持ちます。GitHub、ソースコード、画面共有へ載せないでください。
Secretsに両方の値がある場合はSupabase、ない場合はローカルSQLiteを自動使用します。
