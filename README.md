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
- 音盤棚の一覧からジャンルを選択・一括編集
- 全アルバムから曲のお気に入り度を選んでプレイリスト候補を抽出・CSV出力
- 全収録曲を曲名・演者・作曲者・アルバム・規格品番などから横断検索
- 楽曲検索欄はEnter一回で入力中の検索語を即時反映
- 楽曲検索は既定のフレーズ一致と、複数項目を横断する全単語一致を選択可能
- Cover Art Archiveから盤に対応する表ジャケットを取得・表示
- 国内盤／輸入盤、規格品番、レーベル、演奏者、作曲家、保管場所などを記録
- 全項目横断検索、盤種・出自・ジャンルで絞り込み
- バーコード／規格品番による重複候補の検出
- 登録後の全項目編集、削除、CSV入出力
- 総タイトル数、総ディスク枚数、SACD数を集計
- Supabase Authのメールアドレス・パスワードでログインし、RLSで本人のデータだけを表示

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

新規プロジェクトではSupabaseのSQL Editorで `supabase_schema.sql` を実行します。

既存データがあるプロジェクトでは、データを失わないよう次の順序で移行します。

1. Project SettingsのAPI KeysからPublishable key（旧形式ではanon key）を取得します。
2. Streamlit Cloudの `App settings > Secrets` へ次を追加します。この時点では旧`SUPABASE_SERVICE_ROLE_KEY`をまだ残します。

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_KEY = "YOUR_PUBLISHABLE_OR_ANON_KEY"
```

3. 認証対応版をデプロイします。
4. 起動画面の「新規登録」からメールアドレスとパスワードを登録し、ログインします。
5. 設定タブ、または移行前の接続エラー画面に表示されるユーザーUUIDをコピーします。
6. `supabase_auth_migration.sql` の `owner_id` をそのUUIDへ置き換え、Supabase SQL Editorで実行します。
7. アプリを再読み込みし、既存データが見えることを確認します。
8. 確認後、`SUPABASE_SERVICE_ROLE_KEY`をSecretsから削除します。

認証対応版はログインユーザーのJWTとRLSを使い、本人のalbums・tracksだけを取得・変更します。

自分のアカウント作成後、第三者の新規登録を止める場合はSupabaseのAuthentication設定で新規サインアップを無効にしてください。ログイン済みユーザーには影響しません。
