#!/usr/bin/env python3
"""
Comprehensive Playwright Layout & Safari (WebKit) Audit Script.
Tests WebKit and Chromium across 5 viewports:
- 1440x900 (Desktop Large)
- 1280x800 (Laptop)
- 1024x768 (Tablet)
- 720x800 (Narrow Safari Window)
- 390x844 (Mobile)

Strictly verifies:
1. Zero Emojis in Rendered DOM & Codebase
2. Rendered Safe Markdown (No literal ### or **)
3. Coordinator Escalation Badge Bounding-Box (Zero clipping or horizontal overflow)
4. Visible Brand Wordmark Strictly "SPLUN // CONSULTANT"
5. Topbar 8-Control Geometry:
   - Visibility & Non-Zero Size (w >= 24, h >= 20)
   - Strict Viewport & Parent Containment (left >= 0, right <= window.innerWidth, bottom <= topbar.bottom + 1)
   - Zero Pairwise Overlap across all pairs
   - Real Hit-Target Reachability via document.elementFromPoint
   - Drawer toggle test at 720x800 in WebKit
"""
import sys
import re
import os
import urllib.request
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8000"
EMOJI_PATTERN = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")

VIEWPORTS = [
    {"name": "Desktop Large (1440x900)", "width": 1440, "height": 900, "is_desktop": True},
    {"name": "Laptop (1280x800)",        "width": 1280, "height": 800, "is_desktop": True},
    {"name": "Tablet (1024x768)",        "width": 1024, "height": 768, "is_tablet": True},
    {"name": "Narrow Safari (720x800)",  "width": 720,  "height": 800, "is_narrow": True},
    {"name": "Mobile (390x844)",         "width": 390,  "height": 844, "is_mobile": True},
]

TOPBAR_CONTROLS = [
    {"name": "Left Toggle",        "sel": "#btn-toggle-left, #nav-toggle-workspace",   "is_interactive": True},
    {"name": "Brand Wordmark",     "sel": "#brand-wordmark",                           "is_interactive": False},
    {"name": "Project Selector",   "sel": "#project-select",                           "is_interactive": True},
    {"name": "+ New Project",      "sel": "#btn-new-project",                          "is_interactive": True},
    {"name": "Load Demo",          "sel": "#btn-load-demo",                            "is_interactive": True},
    {"name": "KPI Pill",           "sel": "#kpi-pill, .top-kpi-pill",                  "is_interactive": False},
    {"name": "Export .ai-memory",  "sel": "#btn-export-memory",                        "is_interactive": True},
    {"name": "Right Brain Toggle", "sel": "#btn-toggle-right, #nav-toggle-brain",      "is_interactive": True},
]

def check_disk_emojis():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    emoji_files = []
    for root, dirs, files in os.walk(repo_root):
        if any(skip in root for skip in [".venv", ".git", ".system_generated", "node_modules"]):
            continue
        for file in files:
            if file.endswith((".py", ".html", ".js", ".css", ".sql")):
                p = os.path.join(root, file)
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                matches = EMOJI_PATTERN.findall(content)
                if matches:
                    emoji_files.append((p, matches))
    return emoji_files

def seed_demo_warehouse72():
    req = urllib.request.Request(
        f"{BASE_URL}/api/projects/demo/warehouse72",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status == 200

def run_tests():
    print("\n--- Pre-flight Check: Disk Codebase Emojis ---")
    emoji_files = check_disk_emojis()
    if emoji_files:
        for p, matches in emoji_files:
            print(f"  FAIL: Found emojis in {p}: {matches}")
        raise AssertionError(f"Codebase contains emojis in {len(emoji_files)} files!")
    print("  PASS: 0 emojis found across all .py, .html, .js, .css, .sql files on disk.")

    print("\n--- Pre-flight Check: Re-seeding Warehouse 72 via API ---")
    assert seed_demo_warehouse72(), "Failed to seed warehouse-72 demo via API"
    print("  PASS: Warehouse 72 demo successfully seeded.")

    results = {}
    total_js_errors = 0
    narrow_safari_elements = []

    with sync_playwright() as p:
        for engine_name in ["webkit", "chromium"]:
            engine = getattr(p, engine_name)
            browser = engine.launch(headless=True)
            results[engine_name] = []

            print(f"\n==========================================")
            print(f" Testing Engine: {engine_name.upper()}")
            print(f"==========================================")

            for vp in VIEWPORTS:
                print(f"\n--- Testing Viewport: {vp['name']} ({vp['width']}x{vp['height']}) ---")
                context = browser.new_context(viewport={"width": vp["width"], "height": vp["height"]})
                page = context.new_page()

                js_errors = []
                page.on("pageerror", lambda err: js_errors.append(str(err)))
                page.on("dialog", lambda d: d.accept())

                page.goto(BASE_URL, wait_until="networkidle")
                page.wait_for_timeout(400)

                # Ensure warehouse-72 is selected
                project_select = page.locator("#project-select")
                if project_select.count() > 0:
                    val = project_select.input_value()
                    if val != "warehouse-72":
                        try:
                            project_select.select_option("warehouse-72")
                            page.wait_for_timeout(600)
                        except Exception:
                            pass

                # 1. Visible Brand Wordmark
                brand_wordmark = page.locator("#brand-wordmark").inner_text().strip()
                assert brand_wordmark == "SPLUN // CONSULTANT", f"Expected 'SPLUN // CONSULTANT', got '{brand_wordmark}'"

                # 2. Strict Topbar Per-Control Bounding Box, Overlap & Hit-Target Verification
                topbar_eval = page.evaluate("""() => {
                    const topbar = document.querySelector("header");
                    const topbarRect = topbar.getBoundingClientRect();
                    
                    const selectors = [
                        { name: "Left Toggle", sel: "#btn-toggle-left, #nav-toggle-workspace", isInteractive: true },
                        { name: "Brand Wordmark", sel: "#brand-wordmark", isInteractive: false },
                        { name: "Project Selector", sel: "#project-select", isInteractive: true },
                        { name: "+ New Project", sel: "#btn-new-project", isInteractive: true },
                        { name: "Load Demo", sel: "#btn-load-demo", isInteractive: true },
                        { name: "KPI Pill", sel: "#kpi-pill, .top-kpi-pill", isInteractive: false },
                        { name: "Export .ai-memory", sel: "#btn-export-memory", isInteractive: true },
                        { name: "Right Brain Toggle", sel: "#btn-toggle-right, #nav-toggle-brain", isInteractive: true },
                    ];
                    
                    const items = [];
                    for (const s of selectors) {
                        const el = document.querySelector(s.sel);
                        if (!el) {
                            return { error: `Element not found: ${s.name} (${s.sel})` };
                        }
                        const r = el.getBoundingClientRect();
                        const cs = window.getComputedStyle(el);
                        
                        let hitOk = true;
                        let hitTag = "";
                        if (s.isInteractive) {
                            const cx = r.left + r.width / 2;
                            const cy = r.top + r.height / 2;
                            const hit = document.elementFromPoint(cx, cy);
                            hitOk = hit && (hit === el || el.contains(hit));
                            const cname = typeof hit?.className === "string" ? hit.className : (hit?.className?.baseVal || "");
                            hitTag = hit ? (hit.tagName + (hit.id ? "#" + hit.id : "") + (cname ? "." + cname.split(" ")[0] : "")) : "none";
                        }
                        
                        items.push({
                            name: s.name,
                            left: Math.round(r.left * 10) / 10,
                            top: Math.round(r.top * 10) / 10,
                            right: Math.round(r.right * 10) / 10,
                            bottom: Math.round(r.bottom * 10) / 10,
                            width: Math.round(r.width * 10) / 10,
                            height: Math.round(r.height * 10) / 10,
                            scrollWidth: el.scrollWidth,
                            clientWidth: el.clientWidth,
                            display: cs.display,
                            visibility: cs.visibility,
                            opacity: cs.opacity,
                            isInteractive: s.isInteractive,
                            hitOk: hitOk,
                            hitTag: hitTag
                        });
                    }
                    
                    const overlaps = [];
                    for (let i = 0; i < items.length; i++) {
                        for (let j = i + 1; j < items.length; j++) {
                            const a = items[i];
                            const b = items[j];
                            const overlapX = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
                            const overlapY = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
                            const area = Math.round(overlapX * overlapY * 100) / 100;
                            if (area > 0) {
                                overlaps.push({
                                    pair: `${a.name} vs ${b.name}`,
                                    overlapX: Math.round(overlapX * 10) / 10,
                                    overlapY: Math.round(overlapY * 10) / 10,
                                    area: area
                                });
                            }
                        }
                    }
                    
                    return {
                        topbar: {
                            top: Math.round(topbarRect.top * 10) / 10,
                            bottom: Math.round(topbarRect.bottom * 10) / 10,
                            height: Math.round(topbarRect.height * 10) / 10,
                            width: Math.round(topbarRect.width * 10) / 10
                        },
                        items: items,
                        overlaps: overlaps
                    };
                }""")

                assert "error" not in topbar_eval, f"Topbar eval error: {topbar_eval.get('error')}"
                tb_info = topbar_eval["topbar"]
                items = topbar_eval["items"]
                overlaps = topbar_eval["overlaps"]

                print(f"  Topbar Height: {tb_info['height']}px (Container: [0, {tb_info['bottom']}])")
                print(f"  Verified 8 Topbar Controls:")
                for item in items:
                    print(f"    • {item['name']:18s}: left={item['left']:5.1f}, top={item['top']:5.1f}, right={item['right']:5.1f}, bottom={item['bottom']:5.1f}, w={item['width']:5.1f}, h={item['height']:5.1f}, hitOk={item['hitOk']}")
                    
                    # 2.1 Visibility & Non-Zero Size
                    assert item["display"] != "none", f"{item['name']} has display: none"
                    assert item["visibility"] != "hidden", f"{item['name']} has visibility: hidden"
                    assert item["opacity"] != "0", f"{item['name']} has opacity: 0"
                    assert item["width"] >= 24, f"{item['name']} width {item['width']} < 24px"
                    assert item["height"] >= 20, f"{item['name']} height {item['height']} < 20px"

                    # 2.2 Strict Viewport & Parent Containment
                    assert item["left"] >= 0, f"{item['name']} left {item['left']} < 0"
                    assert item["top"] >= 0, f"{item['name']} top {item['top']} < 0"
                    assert item["right"] <= vp["width"] + 1, f"{item['name']} right {item['right']} > viewport width {vp['width']}"
                    assert item["bottom"] <= tb_info["bottom"] + 1, f"{item['name']} bottom {item['bottom']} > topbar bottom {tb_info['bottom']}"
                    assert item["scrollWidth"] <= item["clientWidth"] + 2, f"{item['name']} text clipped! scrollWidth={item['scrollWidth']} > clientWidth={item['clientWidth']}"

                    # 2.3 Interactive Hit Target Check
                    if item["isInteractive"]:
                        assert item["hitOk"], f"Hit target occluded for {item['name']} (elementFromPoint hit: {item['hitTag']})"

                # 2.4 Zero Pairwise Overlaps
                print(f"  Topbar Pairwise Overlaps: {len(overlaps)}")
                assert len(overlaps) == 0, f"Detected {len(overlaps)} overlapping controls on {engine_name} ({vp['name']}): {overlaps}"

                if engine_name == "webkit" and vp["width"] == 720:
                    narrow_safari_elements = items

                # 3. Interactive Drawer Slide Test at 720x800 in WebKit
                if engine_name == "webkit" and vp["width"] == 720:
                    print("  Testing Right Brain Drawer toggle at 720x800 in WebKit...")
                    toggle_btn = page.locator("#btn-toggle-right, #nav-toggle-brain").first
                    toggle_btn.click()
                    page.wait_for_timeout(350)

                    drawer_state = page.evaluate("""() => {
                        const el = document.getElementById("col-right");
                        const r = el.getBoundingClientRect();
                        const cs = window.getComputedStyle(el);
                        return {
                            isOpen: el.classList.contains("open"),
                            right: Math.round(r.right),
                            width: Math.round(r.width),
                            visible: cs.visibility !== "hidden" && cs.display !== "none"
                        };
                    }""")
                    print(f"    Drawer Open State: isOpen={drawer_state['isOpen']}, right={drawer_state['right']}px, width={drawer_state['width']}px")
                    assert drawer_state["isOpen"], "Right Brain drawer should be open after clicking toggle"
                    assert drawer_state["visible"], "Right Brain drawer should be visible"
                    assert drawer_state["right"] <= 720, f"Drawer right {drawer_state['right']} > 720px (clipped)"
                    assert drawer_state["width"] >= 300, f"Drawer width {drawer_state['width']} < 300px"

                    # Close drawer via backdrop click on uncovered region
                    page.locator("#drawer-backdrop").click(position={"x": 50, "y": 400})
                    page.wait_for_timeout(350)
                    closed_state = page.evaluate("() => !document.getElementById('col-right').classList.contains('open')")
                    assert closed_state, "Right Brain drawer should be closed after backdrop click"
                    print("    Drawer Closed Cleanly: PASS")

                # 4. Post Live Chat Message
                test_msg = "What is the current state and what should I work on next?"
                page.locator("#chat-input").fill(test_msg)
                page.locator(".input-box .btn-primary").click()
                try:
                    page.wait_for_selector("#chat-messages h3, #chat-messages h4", timeout=6000)
                except Exception:
                    page.wait_for_timeout(1000)

                # 5. Zero-Emoji Check in Rendered DOM
                body_text = page.evaluate("() => document.body.innerText")
                emojis_in_dom = EMOJI_PATTERN.findall(body_text)
                print(f"  Rendered DOM Emojis Found: {len(emojis_in_dom)}")
                assert len(emojis_in_dom) == 0, f"Found {len(emojis_in_dom)} emojis in rendered DOM on {engine_name} ({vp['name']})"

                # 6. Rendered Safe Markdown Check
                chat_html = page.evaluate("() => document.getElementById('chat-messages').innerHTML")
                chat_text = page.evaluate("() => document.getElementById('chat-messages').innerText")

                has_h3 = "<h3" in chat_html
                has_h4 = "<h4" in chat_html
                has_strong = "<strong" in chat_html
                has_ul_or_ol = "<ul" in chat_html or "<ol" in chat_html

                raw_hash_matches = re.findall(r"(?:^|\n)\s*#{1,6}\s+", chat_text)
                raw_bold_matches = re.findall(r"\*\*[^*]+\*\*", chat_text)

                assert has_h3 or has_h4, f"Chat messages must contain rendered HTML headings (h3/h4)"
                assert has_strong, f"Chat messages must contain rendered <strong> elements"
                assert has_ul_or_ol, f"Chat messages must contain rendered <ul> or <ol> lists"
                assert len(raw_hash_matches) == 0, f"Chat text contains unparsed literal Markdown header markers: {raw_hash_matches}"
                assert len(raw_bold_matches) == 0, f"Chat text contains unparsed literal bold markers: {raw_bold_matches}"

                # 7. Open Right Drawer if narrow/mobile/tablet to inspect Coordinator Escalations
                if vp.get("is_mobile") or vp.get("is_tablet") or vp.get("is_narrow"):
                    btn_brain = page.locator("#btn-toggle-right, #nav-toggle-brain, #chat-toggle-brain").first
                    btn_brain.click()
                    page.wait_for_timeout(350)

                # 8. Coordinator Escalation Badge Bounding-Box Check
                page.locator("#tab-btn-coordinator").click()
                page.wait_for_timeout(350)

                coord_metrics = page.evaluate("""() => {
                    const colRight = document.getElementById('col-right');
                    const rightBox = colRight.getBoundingClientRect();
                    
                    const escalations = Array.from(document.querySelectorAll('#coordinator-escalations-list .escalation-card, #coordinator-escalations-list .badge, #coordinator-escalations-list .escalation-reason'));
                    
                    const overflows = [];
                    escalations.forEach(el => {
                        const r = el.getBoundingClientRect();
                        const isClipped = (r.right > rightBox.right + 2);
                        const hasInternalOverflow = (el.scrollWidth > el.clientWidth + 2);
                        if (isClipped || hasInternalOverflow) {
                            overflows.push({
                                tag: el.tagName,
                                className: el.className,
                                text: el.innerText.slice(0, 40),
                                right: Math.round(r.right),
                                containerRight: Math.round(rightBox.right),
                                isClipped: isClipped,
                                scrollWidth: el.scrollWidth,
                                clientWidth: el.clientWidth
                            });
                        }
                    });

                    return {
                        escalationCount: escalations.length,
                        overflows: overflows,
                        rightBox: { width: Math.round(rightBox.width), right: Math.round(rightBox.right) }
                    };
                }""")

                print(f"  Coordinator Escalations Measured: {coord_metrics['escalationCount']}, Overflows: {len(coord_metrics['overflows'])}")
                assert len(coord_metrics["overflows"]) == 0, f"Found {len(coord_metrics['overflows'])} overflowing escalation elements in {vp['name']} on {engine_name}"

                assert len(js_errors) == 0, f"Encountered JS errors on {engine_name} at {vp['name']}: {js_errors}"
                total_js_errors += len(js_errors)

                results[engine_name].append({
                    "viewport": vp["name"],
                    "width": vp["width"],
                    "height": vp["height"],
                    "topbar_height": tb_info["height"],
                    "overlaps": len(overlaps),
                    "emojis": len(emojis_in_dom),
                    "js_errors": len(js_errors)
                })
                context.close()
            browser.close()

    print("\n============================================================")
    print("ALL BROWSER TESTS (WEBKIT & CHROMIUM) PASSED PERFECTLY!")
    print(f"Total Viewports Tested per Engine: {len(VIEWPORTS)}")
    print("Top Command Bar: 0 Overlaps, 0 Clipped Controls Across All Viewports")
    print(f"Total JavaScript Errors: {total_js_errors}")
    print("============================================================\n")

    return results, narrow_safari_elements

if __name__ == "__main__":
    run_tests()
