"""تولید compose.yml برای هر اینستنس — امنیت، محدودیت منابع و چرخش لاگ."""

_TEMPLATE = """# تولید خودکار توسط Bot Factory — bot_{order_id}
services:
  bot:
    image: {image}
    container_name: bot_{order_id}
    # 🩹 FIX (S2): قبلاً restart: always بود — کانتینرِ متوقف‌شدهٔ دستی، بعد از
    # ری‌استارت دیمون داکر (ری‌بوت سرور/آپگرید داکر) دوباره خودکار بالا می‌آمد
    # در حالی که وضعیت DB هنوز «stopped» بود (ناهم‌خوانی دائمی در پنل ادمین).
    # unless-stopped = استارت خودکار بعد از ری‌بوت، «مگر» اینکه ادمین خودش stop زده باشد.
    restart: unless-stopped
    env_file: .env
    mem_limit: {mem_limit}
    cpus: "{cpus}"
    pids_limit: {pids_limit}
    security_opt:
      - no-new-privileges:true
    logging:
      driver: "json-file"
      options:
        max-size: "{log_size}"
        max-file: "3"
    volumes:
      - ./sessions:/app/sessions
      - ./downloads:/app/downloads
      - ./exports:/app/exports
      - ./banners:/app/banners
      - ./profile_photos:/app/profile_photos
    networks:
      - factory_net

networks:
  factory_net:
    external: true
"""


def render_instance_compose(*, order_id: int, image: str, mem_limit: str, cpus: str,
                            pids_limit: int, log_size: str) -> str:
    return _TEMPLATE.format(
        order_id=int(order_id), image=image, mem_limit=mem_limit, cpus=cpus,
        pids_limit=pids_limit, log_size=log_size,
    )
