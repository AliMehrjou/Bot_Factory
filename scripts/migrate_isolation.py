#!/usr/bin/env python3
import asyncio
import os
import re
import sys
import secrets
from pathlib import Path
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parent.parent))
load_dotenv()

from config import config
from database.engine import get_root_engine
from database import async_session
from sqlalchemy import text
from core.orchestrator import instance_path, docker_cmd, run_cmd

async def run(dry_run=True):
    print("🚀 در حال اجرای اسکریپت مهاجرت امنیتی (Migration Isolation)...")
    if dry_run: print("⚠️ حالت DRY-RUN فعال است. هیچ تغییری اعمال نمی‌شود.\n")
    
    async with async_session() as session:
        orders = (await session.execute(
            text("SELECT id, redis_slot FROM orders WHERE status IN ('deployed', 'stopped')")
        )).all()
        
    engine = get_root_engine()
    
    for row in orders:
        order_id, slot = row
        db_name = config.db_name_for(order_id)
        db_user = f"sender_{order_id}"
        db_pass = secrets.token_urlsafe(16)
        
        redis_user = f"bot_{order_id}"
        redis_pass = secrets.token_urlsafe(16)
        
        env_file = instance_path(order_id) / ".env"
        compose_file = instance_path(order_id) / "compose.yml"
        
        print(f"🔄 بررسی اینستنس #{order_id} ({db_name})")
        
        if not env_file.exists():
            print(f"  ❌ فایل .env یافت نشد.")
            continue
            
        content = env_file.read_text(encoding="utf-8")
        
        if f"DB_USER={db_user}" in content:
            print("  ✅ قبلاً مایگریت شده است. پرش.")
            continue
            
        print(f"  > ساخت کاربر جدید دیتابیس {db_user} ...")
        stmts = [
            f"CREATE USER IF NOT EXISTS '{db_user}'@'%' IDENTIFIED BY '{db_pass}'",
            f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'%'",
            f"GRANT SELECT, INSERT, UPDATE ON `{db_name}`.* TO '{config.FACTORY_APP_USER}'@'%'",
            "FLUSH PRIVILEGES"
        ]
        
        if not dry_run:
            async with engine.connect() as conn:
                for s in stmts: await conn.execute(text(s))
                
        print(f"  > تولید ACL ردیس {redis_user} ...")
        if slot is None:
            print("  ⚠️ اینستنس اسلات ردیس ندارد. پرش از تنظیم ACL ردیس.")
        else:
            redis_host, _ = config.redis_for_slot(slot)
            if not dry_run:
                await run_cmd([
                    "docker", "exec", "-e", f"REDISCLI_AUTH={config.REDIS_PASS}",
                    redis_host, "redis-cli", 
                    "ACL", "SETUSER", redis_user, "on", f">{redis_pass}", "~*", "+@all", "-@dangerous", "-@admin"
                ], timeout=15, check=False)
            
        print(f"  > به‌روزرسانی فایل .env ...")
        new_content = re.sub(r"^DB_USER=.*$", f"DB_USER={db_user}", content, flags=re.M)
        new_content = re.sub(r"^DB_PASS=.*$", f"DB_PASS={db_pass}", new_content, flags=re.M)
        
        # افزودن ردیس یوزر اگر نبود
        if "REDIS_USER=" not in new_content:
            new_content = new_content.replace("REDIS_PASS=", f"REDIS_USER={redis_user}\nREDIS_PASS=")
        else:
            new_content = re.sub(r"^REDIS_USER=.*$", f"REDIS_USER={redis_user}", new_content, flags=re.M)
            
        new_content = re.sub(r"^REDIS_PASS=.*$", f"REDIS_PASS={redis_pass}", new_content, flags=re.M)
        
        if not dry_run:
            env_file.write_text(new_content, encoding="utf-8")
            
        print(f"  > Recreate کانتینر bot_{order_id} ...")
        if not dry_run:
            await run_cmd(docker_cmd() + ["-p", f"bot_{order_id}", "-f", str(compose_file), "up", "-d", "--force-recreate"])
            
    print("\n✅ عملیات مهاجرت به پایان رسید.")

if __name__ == "__main__":
    if "-h" in sys.argv or "--help" in sys.argv:
        print("Usage: python migrate_isolation.py [--apply]")
        print("بدون فلگ --apply، اسکریپت در حالت ایمن (dry-run) اجرا شده و تغییری اعمال نمی‌کند.")
        sys.exit(0)
    
    dry = "--apply" not in sys.argv
    asyncio.run(run(dry_run=dry))