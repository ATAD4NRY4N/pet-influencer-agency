import os
import json
import urllib.parse

DOCS_DIR = "docs"

def build_all_storefronts_and_grounding():
    with open("state/character_bible.json", "r") as f:
        bible = json.load(f)
    with open("state/catalog.json", "r") as f:
        catalog = json.load(f)
    with open("state/strategy_weights.json", "r") as f:
        weights = json.load(f)

    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(os.path.join(DOCS_DIR, "grounding"), exist_ok=True)

    # 1. Build Grounding Universe Bible Page
    grounding_cards = []
    for c_key, c_data in bible.items():
        h = c_data["human"]
        pets_html = "".join([
            f"<div class='pet-card'><strong>{p['name']}</strong> ({p['breed']})<br><small>{p['immutable_marking_dna']}</small></div>"
            for p in c_data.get("pets", [])
        ])
        locs_html = "".join([
            f"<li><strong>{k}:</strong> {v[:90]}...</li>"
            for k, v in c_data.get("locations", {}).items()
        ])
        grounding_cards.append(f"""
        <div class="channel-grounding">
          <h2>{c_data['channel_name']} ({c_data['handle']})</h2>
          <p><strong>Human Creator:</strong> {h['name']} ({h['age']}yo) - {h['immutable_face_dna']}</p>
          <h3>Active Pets</h3>
          <div class="pet-grid">{pets_html}</div>
          <h3>Apartment Locations</h3>
          <ul>{locs_html}</ul>
        </div>
        """)

    grounding_html = f"""<!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Universe Grounding Bible</title>
      <style>
        body {{ background:#0f172a; color:#f8fafc; font-family:-apple-system,BlinkMacSystemFont,sans-serif; padding:24px; max-width:800px; margin:auto; }}
        h1 {{ color:#f59e0b; }} h2 {{ border-bottom:1px solid #334155; padding-bottom:8px; margin-top:24px; }}
        .channel-grounding {{ background:#1e293b; padding:20px; border-radius:12px; margin-bottom:20px; }}
        .pet-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin:10px 0; }}
        .pet-card {{ background:#0f172a; padding:12px; border-radius:8px; border:1px solid #334155; }}
        ul {{ font-size:13px; color:#94a3b8; line-height:1.6; }}
      </style>
    </head>
    <body>
      <h1>🐾 Universe Grounding & Cast Bible</h1>
      <p>Live visual parameters and character definitions powering the AI agency.</p>
      {"".join(grounding_cards)}
    </body>
    </html>"""

    with open(os.path.join(DOCS_DIR, "grounding", "index.html"), "w", encoding="utf-8") as f:
        f.write(grounding_html)

    # 2. Build Public Storefronts
    for c_key, c_data in bible.items():
        slug = c_data["bio_slug"]
        slug_dir = os.path.join(DOCS_DIR, slug)
        os.makedirs(slug_dir, exist_ok=True)
        products = catalog.get(c_key, [])

        prod_html = []
        for p in products:
            prod_html.append(f"""
            <div style="background:#1e293b; padding:16px; border-radius:12px; margin-bottom:12px;">
              <span style="background:#f59e0b; color:#000; font-size:10px; font-weight:800; padding:2px 6px; border-radius:4px;">{p.get('badge', 'Verified')}</span>
              <h3 style="margin:8px 0 4px; font-size:16px;">{p['title']}</h3>
              <p style="color:#34d399; font-weight:700;">{p['price']}</p>
              <a href="{p['affiliate_url']}" target="_blank" style="display:inline-block; margin-top:6px; background:#3b82f6; color:#fff; text-decoration:none; padding:6px 12px; border-radius:6px; font-size:12px; font-weight:600;">View Product →</a>
            </div>
            """)

        store_html = f"""<!DOCTYPE html>
        <html>
        <head>
          <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
          <title>{c_data['channel_name']}</title>
          <style>
            body {{ background:#0f172a; color:#f8fafc; font-family:-apple-system,BlinkMacSystemFont,sans-serif; padding:24px; max-width:440px; margin:auto; }}
          </style>
        </head>
        <body>
          <h1 style="text-align:center;">{c_data['channel_name']}</h1>
          <p style="text-align:center; color:#94a3b8; font-size:14px;">Products seen in our daily videos</p>
          {"".join(prod_html)}
        </body>
        </html>"""

        with open(os.path.join(slug_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(store_html)