#!/usr/bin/env python3
"""
🩹 افزودن VIDEO_LINK به .env اینستنس‌های موجود (رفع باگ منوی راهنما)

مشکل: قالب render_instance_env پارامتر video_link را می‌گرفت ولی هرگز در فایل
.env نمی‌نوشت ← دکمه «🎥 تماشای فیلم آموزش کار با ربات» در منوی راهنمای ربات
مشتریان با URL خالی ساخته می‌شد و کل منو با خطای BUTTON_URL_INVALID از کار می‌افتاد.

این اسکریپت برای همه اینستنس‌های deployed/stopped:
  ۱) اگر VIDEO_LINK در .env نباشد → بعد از LOGIN_PROXY_URL اضافه می‌کند؛
  ۲) اگر باشد ولی با مقدار فعلی فکتوری فرق کند → به‌روزرسانی می‌کند؛
  ۳) کانتینر را با --force-recreate از نو راه‌اندازی می‌کند (env جدید بخواند).

نحوه اجرا:
  python scripts/add_video_link.py           # حالت ایمن dry-run
  python scripts/add_video_link.py --apply   # اعمال واقعی
"""
import asyncio
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parent.parent))
load_dotenv()

from config import config
from database import async_session
from core.orchestrator import instance_path, docker_cmd, run_cmd
from sqlalchemy import text


def _inject_video_link(content: str, video_link: str) -> tuple[str, bool]:
    """تزریق/به‌روزرسانی VIDEO_LINK در محتوای .env — خروجی: (محتوای جدید, تغییرکرد؟)"""
    line = f"VIDEO_LINK={video_link}"

    # ۱) خط موجود → به‌روزرسانی فقط در صورت تفاوت
    m = re.search(r"^VIDEO_LINK=.*$", content, flags=re.M)
    if m:
        if m.group(0) == line:
            return content, False
        return content[:m.start()] + line + content[m.end():], True

    # ۲) خط موجود نیست → بهترین جای درج: بعد از LOGIN_PROXY_URL (قرار قالب جدید)
    anchor = re.search(r"^LOGIN_PROXY_URL=.*$", content, flags=re.M)
    block = (
        "\n# --- ویدیوی آموزشی (دکمه «🎥 تماشای فیلم آموزش» در منوی راهنمای ربات) ---\n"
        f"{line}\n"
    )
    if anchor:
        return content[:anchor.end()] + "\n" + block.rstrip("\n") + content[anchor.end():], True

    # ۳) fallback: انتهای فایل
    if not content.endswith("\n"):
        content += "\n"
    return content + block, True


async def run(dry_run: bool = True):
    video_link = (config.VIDEO_LINK or "").strip()
    print("🚀 افزودن VIDEO_LINK به .env اینستنس‌های موجود...")
    if dry_run:
        print("⚠️ حالت DRY-RUN فعال است. هیچ تغییری اعمال نمی‌شود.\n")

    if not video_link:
        print("❌ VIDEO_LINK در .env فکتوری خالی است. اول آن را در /opt/factory/.env")
        print("   (یا همان جای فکتوری) مقداردهی کنید، سپس دوباره اجرا کنید.")
        print("   نکته: سندربِات اصلاح‌شده با VIDEO_LINK خالی، دکمه ویدیو را حذف می‌کند و")
        print("   منوی راهنما سالم می‌ماند — پس حتی خالی هم اگر بخواهید sync کنید مجاز است.")
        return

    async with async_session() as session:
        orders = (await session.execute(
            text("SELECT id FROM orders WHERE status IN ('deployed', 'stopped')")
        )).all()

    changed = 0
    for (order_id,) in orders:
        env_file = instance_path(order_id) / ".env"
        compose_file = instance_path(order_id) / "compose.yml"
        print(f"🔄 بررسی اینستنس bot_{order_id}")

        if not env_file.exists():
            print("  ❌ فایل .env یافت نشد. پرش.")
            continue

        content = env_file.read_text(encoding="utf-8")
        new_content, touched = _inject_video_link(content, video_link)

        if not touched:
            print("  ✅ VIDEO_LINK از قبل درست است. پرش.")
            continue

        print(f"  > VIDEO_LINK={video_link}")
        if not dry_run:
            env_file.write_text(new_content, encoding="utf-8")
            print(f"  > Recreate کانتینر bot_{order_id} ...")
            await run_cmd(
                docker_cmd() + ["-p", f"bot_{order_id}", "-f", str(compose_file),
                                "up", "-d", "--force-recreate"],
                timeout=180, check=False,
            )
        changed += 1

    print(f"\n✅ پایان — {changed} اینستنس {'نیازمند' if dry_run else 'در'} به‌روزرسانی.")


if __name__ == "__main__":
    if "-h" in sys.argv or "--help" in sys.argv:
        print("Usage: python add_video_link.py [--apply]")
        print("بدون فلگ --apply، اسکریپت در حالت ایمن (dry-run) اجرا شده و تغییری اعمال نمی‌کند.")
        sys.exit(0)

    asyncio.run(run(dry_run="--apply" not in sys.argv))
