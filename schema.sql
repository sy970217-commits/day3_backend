-- Supabase 대시보드 > SQL Editor 에 붙여넣고 Run 하세요. (여러 번 실행해도 안전)

create table if not exists public.products (
  id          bigint generated always as identity primary key,
  name        text    not null,
  description text    not null default '',
  price       integer not null check (price >= 0),
  emoji       text    not null default '🍊'
);

create table if not exists public.cart_items (
  id          bigint generated always as identity primary key,
  session_id  text    not null,
  product_id  bigint  not null references public.products(id) on delete cascade,
  quantity    integer not null check (quantity between 1 and 99),
  created_at  timestamptz not null default now(),
  unique (session_id, product_id)
);

create index if not exists cart_items_session_idx on public.cart_items(session_id);

-- 브라우저(공개 키)에서 직접 접근 못 하게 잠금. 서버(secret key)는 RLS를 우회하므로 정상 동작.
alter table public.products   enable row level security;
alter table public.cart_items enable row level security;

-- 샘플 상품 (비어 있을 때만 입력)
insert into public.products (name, description, price, emoji)
select * from (values
  ('한라봉 선물세트 3kg', '새콤달콤 향긋한 제주 대표 감귤', 25000, '🍊'),
  ('노지 감귤 5kg',       '당도 높은 겨울 제철 노지 감귤', 18000, '🍋'),
  ('천혜향 2kg',          '껍질이 얇고 즙이 풍부해요',     28000, '🍑'),
  ('흑돼지 오겹살 1kg',   '쫄깃하고 고소한 제주 흑돼지',   32000, '🐖'),
  ('은갈치 2미',          '산지 직송 싱싱한 은갈치',       45000, '🐟'),
  ('오메기떡 10개입',     '팥고물 가득, 쫀득한 전통 떡',   15000, '🍡'),
  ('제주 녹차 티백',      '오설록 스타일 은은한 녹차',     12000, '🍵'),
  ('감귤 초콜릿 세트',    '여행 선물로 인기 만점',         14000, '🎁')
) as v(name, description, price, emoji)
where not exists (select 1 from public.products);
