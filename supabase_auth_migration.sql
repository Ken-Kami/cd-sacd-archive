-- 既存の音盤棚をSupabase Auth + RLSへ移行するスクリプトです。
-- 1. 認証対応版の起動画面で自分のAuthユーザーを新規登録し、ログインします。
-- 2. アプリの設定タブ、または移行前の接続エラー画面に表示されるユーザーUUIDをコピーします。
-- 3. 下の owner_id のUUIDを置き換えてから、SQL Editorで全体を実行します。

BEGIN;

ALTER TABLE public.albums
  ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.tracks
  ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE;

DO $$
DECLARE
  owner_id UUID := '00000000-0000-0000-0000-000000000000'; -- 自分のUUIDへ必ず置換
BEGIN
  IF owner_id = '00000000-0000-0000-0000-000000000000'::UUID THEN
    RAISE EXCEPTION 'owner_idをSupabase Authの自分のUUIDへ置き換えてください';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM auth.users WHERE id = owner_id) THEN
    RAISE EXCEPTION '指定したUUIDのAuthユーザーが見つかりません';
  END IF;

  UPDATE public.albums SET user_id = owner_id WHERE user_id IS NULL;
  UPDATE public.tracks t
     SET user_id = a.user_id
    FROM public.albums a
   WHERE t.album_id = a.id AND t.user_id IS NULL;
END $$;

ALTER TABLE public.albums ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE public.tracks ALTER COLUMN user_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_albums_user_id ON public.albums(user_id);
CREATE INDEX IF NOT EXISTS idx_tracks_user_id ON public.tracks(user_id);

ALTER TABLE public.albums ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tracks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.albums, public.tracks FROM anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.albums, public.tracks TO authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;

DROP POLICY IF EXISTS "users read own albums" ON public.albums;
DROP POLICY IF EXISTS "users insert own albums" ON public.albums;
DROP POLICY IF EXISTS "users update own albums" ON public.albums;
DROP POLICY IF EXISTS "users delete own albums" ON public.albums;
CREATE POLICY "users read own albums" ON public.albums FOR SELECT TO authenticated
USING ((SELECT auth.uid()) = user_id);
CREATE POLICY "users insert own albums" ON public.albums FOR INSERT TO authenticated
WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE POLICY "users update own albums" ON public.albums FOR UPDATE TO authenticated
USING ((SELECT auth.uid()) = user_id) WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE POLICY "users delete own albums" ON public.albums FOR DELETE TO authenticated
USING ((SELECT auth.uid()) = user_id);

DROP POLICY IF EXISTS "users read own tracks" ON public.tracks;
DROP POLICY IF EXISTS "users insert own tracks" ON public.tracks;
DROP POLICY IF EXISTS "users update own tracks" ON public.tracks;
DROP POLICY IF EXISTS "users delete own tracks" ON public.tracks;
CREATE POLICY "users read own tracks" ON public.tracks FOR SELECT TO authenticated
USING ((SELECT auth.uid()) = user_id);
CREATE POLICY "users insert own tracks" ON public.tracks FOR INSERT TO authenticated
WITH CHECK (
  (SELECT auth.uid()) = user_id
  AND EXISTS (
    SELECT 1 FROM public.albums a
    WHERE a.id = album_id AND a.user_id = (SELECT auth.uid())
  )
);
CREATE POLICY "users update own tracks" ON public.tracks FOR UPDATE TO authenticated
USING ((SELECT auth.uid()) = user_id)
WITH CHECK (
  (SELECT auth.uid()) = user_id
  AND EXISTS (
    SELECT 1 FROM public.albums a
    WHERE a.id = album_id AND a.user_id = (SELECT auth.uid())
  )
);
CREATE POLICY "users delete own tracks" ON public.tracks FOR DELETE TO authenticated
USING ((SELECT auth.uid()) = user_id);

COMMIT;
