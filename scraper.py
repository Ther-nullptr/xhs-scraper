#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书博主主页内容抓取脚本 (Playwright, 半自动, 通用)

适用于任意博主: 你导航到谁的主页, 就抓谁。

流程:
  1. 启动一个"持久化登录态"的真实 Chromium 窗口 (扫一次码以后免登录)
  2. 你在浏览器里登录 + 搜索任意博主 + 点进 TA 的主页
  3. 回到终端按回车, 脚本接管:
       - 自动识别博主 (昵称 + userId)
       - 像人一样逐张点开笔记封面 -> 浮层
       - 从 window.__INITIAL_STATE__ 提取 标题/正文/高清图(视频)
       - 用已登录上下文 + Referer 下载 (规避防盗链)
       - Esc 关闭, 处理下一篇
  4. 结果写入 output/<昵称>_<userId>/<noteId>_<标题>/  (text.md + note.json + 图片/视频)
  5. 断点续传: 重复运行会跳过已下载的笔记, 可分批抓 / 补抓

用法:
  python scraper.py            # 抓当前主页全部笔记
  python scraper.py --max 5    # 只抓前 5 篇 (先小范围验证)
  python scraper.py --inspect  # 诊断模式: 点开第一篇并 dump 数据

⚠️ 仅供个人学习/备份你有权访问的内容. 请控制频率、不要商用或二次传播, 风险自负.
"""
import asyncio
import json
import re
import random
import argparse
from pathlib import Path
from playwright.async_api import async_playwright

BASE = Path(__file__).resolve().parent
USER_DATA_DIR = BASE / "browser-data"   # 持久化登录态目录
OUT_DIR = BASE / "output"

NOTE_ID_RE = re.compile(r"[0-9a-fA-F]{24}")
REFERER = "https://www.xiaohongshu.com/"

CT_EXT = {
    "image/webp": ".webp",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
}


def sanitize(name: str, limit: int = 60) -> str:
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]", "_", name or "").strip()
    return (name[:limit] or "untitled").strip()


async def human_sleep(a=0.8, b=2.0):
    await asyncio.sleep(random.uniform(a, b))


EXTRACT_JS = """(noteId) => {
    try {
        const st = window.__INITIAL_STATE__;
        const m = st && st.note && st.note.noteDetailMap;
        if (!m) return JSON.stringify({error: 'no noteDetailMap'});
        let entry = m[noteId];
        if (!entry) {
            const vals = Object.values(m);
            entry = vals.length ? vals[vals.length - 1] : null;
        }
        if (!entry) return JSON.stringify({error: 'no entry'});
        const note = entry.note || entry;
        const imgs = (note.imageList || []).map(it => {
            if (it.urlDefault) return it.urlDefault;
            if (it.urlPre) return it.urlPre;
            const info = it.infoList || [];
            return info.length ? info[info.length - 1].url : null;
        }).filter(Boolean);
        let videos = [];
        try {
            const stream = note.video && note.video.media && note.video.media.stream;
            if (stream) {
                for (const codec of ['h264','h265','av1']) {
                    const arr = stream[codec] || [];
                    if (arr.length && arr[0].masterUrl) { videos.push(arr[0].masterUrl); break; }
                }
            }
        } catch(e) {}
        return JSON.stringify({
            noteId, type: note.type || 'normal',
            title: note.title || '', desc: note.desc || '',
            images: imgs, videos
        });
    } catch(e) { return JSON.stringify({error: String(e)}); }
}"""


async def inspect_first_note(page):
    """诊断: 点开第一篇笔记浮层, 验证从 __INITIAL_STATE__ 提取数据。"""
    await human_sleep(1.0, 2.0)
    for _ in range(3):
        await page.mouse.wheel(0, 1000)
        await human_sleep(0.6, 1.0)
    await page.mouse.wheel(0, -3000)
    await human_sleep(1.0, 1.5)

    OUT_DIR.mkdir(exist_ok=True)
    sel = "a.cover:visible"
    n = await page.locator(sel).count()
    print(f"  可见封面卡片 a.cover: {n} 个")
    if n == 0:
        print("  没找到可见封面，无法继续。")
        return
    loc = page.locator(sel).first
    href = await loc.get_attribute("href")
    ids = NOTE_ID_RE.findall(href or "")
    note_id = ids[-1] if ids else ""
    print(f"  第一篇 href: {href}")
    print(f"  noteId: {note_id}")

    try:
        await loc.click(timeout=8000, no_wait_after=True)
    except Exception as e:
        print(f"  click 异常(忽略): {e}")
    await human_sleep(3.0, 4.5)
    print(f"  点击后 URL: {page.url}")

    data = await page.evaluate(EXTRACT_JS, note_id)
    (OUT_DIR / "_debug_note_data.json").write_text(data, encoding="utf-8")
    print(f"  提取结果: {data[:400]}")
    try:
        html = await page.content()
        (OUT_DIR / "_debug_note.html").write_text(html, encoding="utf-8")
        print(f"  已存 _debug_note.html ({len(html)} 字节)")
    except Exception as e:
        print(f"  dump html 失败: {e}")


async def get_author(page) -> tuple[str, str]:
    """从当前主页识别 (userId, 昵称)。"""
    info = await page.evaluate(
        r"""() => {
            const m = location.pathname.match(/user\/profile\/([0-9a-fA-F]{24})/);
            const uid = m ? m[1] : 'unknown';
            let nick = '';
            try {
                const t = document.title || '';
                nick = t.replace(/[（(]小红书号[:：].*$/, '')
                        .replace(/\s*-\s*小红书.*$/, '').trim();
            } catch (e) {}
            return JSON.stringify({uid, nick});
        }"""
    )
    try:
        d = json.loads(info)
    except Exception:
        d = {}
    return d.get("uid", "unknown"), d.get("nick", "")


async def save_note(ctx, data: dict, base_dir: Path) -> tuple[int, int]:
    """把提取到的笔记数据落盘 (note.json + text.md + 图片/视频)。"""
    title = data.get("title") or ""
    nid = data.get("noteId") or "unknown"
    folder = base_dir / f"{nid}_{sanitize(title, 40)}"
    folder.mkdir(parents=True, exist_ok=True)

    (folder / "note.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = [f"# {title or '(无标题)'}\n", data.get("desc", ""), f"\n\n> noteId: {nid}\n"]
    (folder / "text.md").write_text("\n".join(md), encoding="utf-8")

    n_img = 0
    for i, url in enumerate(data.get("images", []) or [], 1):
        if await download_binary(ctx, url, folder / f"img_{i:02d}"):
            n_img += 1
        await human_sleep(0.3, 0.7)
    n_vid = 0
    for i, url in enumerate(data.get("videos", []) or [], 1):
        dest = await download_binary(ctx, url, folder / f"video_{i:02d}")
        if dest:
            dest.rename(dest.with_suffix(".mp4"))
            n_vid += 1
    return n_img, n_vid


async def ensure_modal_closed(page):
    """关闭笔记浮层, 确保回到主页。"""
    for _ in range(4):
        if "/explore/" not in page.url:
            return
        await page.keyboard.press("Escape")
        await human_sleep(0.6, 1.0)
        if "/explore/" in page.url:
            try:
                await page.go_back()
            except Exception:
                pass
            await human_sleep(0.6, 1.0)


async def scrape_profile(page, ctx, max_notes: int, base_dir: Path) -> int:
    """遍历主页所有笔记: 点开封面 -> 提取 -> 下载 -> 关闭。"""
    base_dir.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    # 断点续传: 已有的笔记目录直接跳过
    for d in base_dir.iterdir():
        if d.is_dir():
            mm = NOTE_ID_RE.search(d.name)
            if mm:
                done.add(mm.group(0))
    if done:
        print(f"  续传: 已存在 {len(done)} 篇, 本次将跳过它们。")
    new_count = 0
    no_progress = 0
    print("  开始遍历笔记 ...")
    while True:
        if max_notes and len(done) >= max_notes:
            break
        covers = await page.eval_on_selector_all(
            "a.cover",
            "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
        )
        target_id = None
        for h in covers:
            ids = NOTE_ID_RE.findall(h)
            if not ids:
                continue
            nid = ids[-1]
            if nid not in done:
                target_id = nid
                break

        if target_id is None:                 # 当前视图无新笔记 -> 继续往下滚
            await page.mouse.wheel(0, 2200)
            await human_sleep(1.0, 1.8)
            no_progress += 1
            if no_progress >= 6:
                print("  没有更多新笔记，结束遍历。")
                break
            continue

        no_progress = 0
        done.add(target_id)
        n = len(done)

        try:
            loc = page.locator(f'a.cover[href*="{target_id}"]').first
            await loc.scroll_into_view_if_needed(timeout=8000)
            await loc.click(timeout=8000, no_wait_after=True)
        except Exception as e:
            print(f"  [{n}] {target_id} 点击失败: {e}")
            await ensure_modal_closed(page)
            continue

        data = None
        for _ in range(12):                   # 轮询等待浮层数据就绪
            await asyncio.sleep(0.7)
            try:
                obj = json.loads(await page.evaluate(EXTRACT_JS, target_id))
            except Exception:
                obj = {}
            if obj and not obj.get("error") and (
                obj.get("images") or obj.get("desc") or obj.get("title")
            ):
                data = obj
                break

        if data is None:
            print(f"  [{n}] {target_id} 提取失败 (跳过)")
        else:
            n_img, n_vid = await save_note(ctx, data, base_dir)
            new_count += 1
            print(f"  [{n}] ✓ {sanitize(data.get('title',''), 24)}  图{n_img} 视频{n_vid}")

        await ensure_modal_closed(page)
        await human_sleep(1.5, 3.0)           # 礼貌间隔, 降低风控风险

    return new_count


async def download_binary(ctx, url: str, dest_no_ext: Path) -> Path | None:
    try:
        resp = await ctx.request.get(url, headers={"Referer": REFERER})
        if not resp.ok:
            print(f"      ✗ 下载失败 {resp.status}: {url[:80]}")
            return None
        data = await resp.body()
        ct = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        ext = CT_EXT.get(ct, ".jpg")
        dest = dest_no_ext.with_suffix(ext)
        dest.write_bytes(data)
        return dest
    except Exception as e:
        print(f"      ✗ 下载异常: {e}")
        return None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="直接指定博主主页 URL (可选); 不填则手动导航后回车", default=None)
    ap.add_argument("--max", type=int, default=0, help="最多抓取多少篇 (0=全部)")
    ap.add_argument("--inspect", action="store_true", help="诊断模式: 点开第一篇笔记并 dump HTML/截图")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    USER_DATA_DIR.mkdir(exist_ok=True)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        start_url = args.url or "https://www.xiaohongshu.com/explore"
        await page.goto(start_url, wait_until="domcontentloaded")

        print("\n" + "=" * 64)
        print("请在弹出的浏览器窗口里:")
        print("  1) 若未登录 -> 扫码登录 (只需一次, 之后免登录)")
        print("  2) 搜索目标博主 -> 点进 TA 的主页")
        print("  3) 确认当前页面停在博主【主页】, 然后回到这里按 Enter")
        print("  (想抓多个博主时, 抓完一个再导航到下一个重新运行即可)")
        print("=" * 64)
        await asyncio.get_event_loop().run_in_executor(None, input, "准备好后按 Enter 开始抓取 ...")

        # 小红书常在新标签页打开主页, 所以从所有打开的标签里挑选目标页
        await human_sleep(0.4, 0.8)
        open_pages = [pg for pg in ctx.pages if not pg.is_closed()]
        print(f"\n当前打开的标签页 ({len(open_pages)} 个):")
        for pg in open_pages:
            print(f"   - {pg.url}")

        target = None
        for pg in open_pages:                       # 优先选主页
            if "user/profile" in pg.url:
                target = pg
                break
        if target is None and open_pages:           # 否则用最后打开的
            target = open_pages[-1]
        if target is None:
            print("没有可用的标签页，退出。")
            await ctx.close()
            return

        page = target
        await page.bring_to_front()
        print(f"\n选定页面: {page.url}")
        if "user/profile" not in page.url:
            print("⚠️ 选定页面不是用户主页 (URL 不含 user/profile)。")
            print("   请先点进某个博主的【主页】再运行。仍会尝试在本页处理。")

        if args.inspect:
            await inspect_first_note(page)
            await ctx.close()
            return

        uid, nick = await get_author(page)
        base_dir = OUT_DIR / sanitize(f"{nick}_{uid}" if nick else uid, 80)
        print(f"\n博主: {nick or '(未识别到昵称)'}   uid={uid}")
        print(f"输出目录: {base_dir}")

        n = await scrape_profile(page, ctx, args.max, base_dir)
        print(f"\n完成: 本次新增 {n} 篇。结果在: {base_dir}")
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
