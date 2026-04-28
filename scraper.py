#!/usr/bin/env python3
"""
Government Notice Scraper - People.cn Anti-Formalism Platform
Scrapes articles, extracts content, classifies via AI, stores in PostgreSQL.
"""

import json
import re
import subprocess
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from config import *

def pg_execute(sql, params=None, fetch=False):
    """Execute SQL on notices database"""
    # Escape single quotes in SQL
    sql_escaped = sql.replace("'", "'\\''")
    cmd = f"sudo -A docker exec -e PGPASSWORD='{PG_PASSWORD}' 1Panel-postgresql-qRXy psql -U {PG_USER} -h localhost -d {PG_DB} -t -A -F'|' -c '{sql_escaped}'"
    
    env = os.environ.copy()
    env['SUDO_ASKPASS'] = '/tmp/ssh_pass_ca.sh'
    env['DISPLAY'] = 'none'
    
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, env=env)
    if r.returncode != 0:
        print(f"SQL Error: {r.stderr[:200]}")
        return None
    return r.stdout.strip() if fetch else "OK"

def fetch_page(url):
    """Fetch page content using curl, handle encoding"""
    r = subprocess.run(
        ['curl', '-s', '-m', '15', '-L', url],
        capture_output=True, timeout=20
    )
    if r.returncode != 0:
        return None
    # Try UTF-8 first, then GBK
    try:
        return r.stdout.decode('utf-8')
    except UnicodeDecodeError:
        try:
            return r.stdout.decode('gbk')
        except:
            return r.stdout.decode('gb18030', errors='replace')

def extract_article_links(html, keyword):
    """Extract links from list page that match keyword"""
    links = []
    # Pattern: <a href="URL">TITLE</a>
    pattern = r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>'
    for match in re.finditer(pattern, html):
        url, title = match.group(1), match.group(2).strip()
        if keyword in title:
            # Make absolute URL
            if url.startswith('/'):
                url = 'http://zzxszy.people.cn' + url
            elif not url.startswith('http'):
                url = 'http://zzxszy.people.cn/' + url
            links.append({'url': url, 'title': title})
    
    # Deduplicate by URL
    seen = set()
    unique = []
    for link in links:
        if link['url'] not in seen:
            seen.add(link['url'])
            unique.append(link)
    return unique

def extract_article_content(html):
    """Extract title, date, content from article page"""
    # Extract title
    title_match = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""
    title = re.sub(r'<[^>]+>', '', title).strip()
    
    # Extract date - patterns like 2024年04月08日16:01
    date_match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', html)
    pub_date = None
    if date_match:
        try:
            pub_date = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
        except:
            pass
    
    # Extract content - try multiple patterns
    content = ""
    # Pattern 1: article body div
    body_match = re.search(r'<div[^>]*class=["\'][^"\']*rm_txt_con[^"\']*["\'][^>]*>(.*?)</div>', html, re.DOTALL)
    if not body_match:
        body_match = re.search(r'<div[^>]*class=["\'][^"\']*content[^"\']*["\'][^>]*>(.*?)</div>', html, re.DOTALL)
    if not body_match:
        # Try finding paragraphs between h1 and footer
        body_match = re.search(r'</h1>(.*?)<(?:div|footer)[^>]*class=["\'][^"\']*(?:footer|copyright)', html, re.DOTALL)
    
    if body_match:
        content = body_match.group(1)
        # Clean HTML tags
        content = re.sub(r'<[^>]+>', '\n', content)
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = re.sub(r'(责编：.*?[\)）]|人民网.*?版权所有|Copyright.*?rights reserved)', '', content)
        content = content.strip()
    
    return {'title': title, 'pub_date': pub_date, 'content': content}

def classify_with_ai(title, content):
    """Use Ollama to classify the notice"""
    prompt = f"""分析以下政府通报，返回JSON格式：
标题：{title}
内容摘要：{content[:1000]}

请返回以下字段的JSON：
{{
  "level_1": "中央" 或 省份名称（如"山西"、"辽宁"等），
  "level_2": ["问题类型1", "问题类型2"]（如：层层加码、数据造假、形式主义、官僚主义、脱离实际、资源浪费、强制摊派、考核泛滥），
  "provinces": ["涉及省份1", "涉及省份2"],
  "case_count": 通报案例数量,
  "summary": "一句话摘要（50字以内）"
}}
只返回JSON，不要其他内容。"""
    
    try:
        r = subprocess.run(
            ['curl', '-s', '-m', '30', OLLAMA_URL,
             '-H', 'Content-Type: application/json',
             '-d', json.dumps({"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.1}})],
            capture_output=True, text=True, timeout=60
        )
        data = json.loads(r.stdout)
        response = data.get('response', '')
        # Extract JSON from response
        json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        print(f"  AI分类失败: {e}")
    
    return {
        "level_1": "中央",
        "level_2": [],
        "provinces": [],
        "case_count": 0,
        "summary": title[:50]
    }

def check_exists(url):
    """Check if URL already in database"""
    sql = f"SELECT id FROM notices WHERE source_url = '{url}'"
    result = pg_execute(sql, fetch=True)
    return bool(result)

def insert_notice(notice):
    """Insert a notice into database"""
    sql = f"""INSERT INTO notices (title, source_url, level_1, level_2, publish_date, content, summary, tags, provinces, case_count, ai_processed)
    VALUES (
        '{notice["title"].replace("'", "''")}',
        '{notice["source_url"].replace("'", "''")}',
        '{notice.get("level_1", "中央").replace("'", "''")}',
        ARRAY{notice.get("level_2", [])}::text[],
        {'NULL' if not notice.get("pub_date") else f"'{notice['pub_date']}'"},
        '{notice.get("content", "").replace("'", "''")[:5000]}',
        '{notice.get("summary", "").replace("'", "''")}',
        ARRAY{notice.get("tags", notice.get("level_2", []))}::text[],
        ARRAY{notice.get("provinces", [])}::text[],
        {notice.get("case_count", 0)},
        {str(notice.get("ai_processed", False)).lower()}
    ) ON CONFLICT (source_url) DO NOTHING;"""
    return pg_execute(sql)

def main():
    print("🔍 开始抓取人民政协网案例通报...")
    print(f"   目标: {PEOPLE_CN_LIST}")
    print(f"   关键词: {SEARCH_KEYWORD}")
    
    # Fetch list page
    html = fetch_page(PEOPLE_CN_LIST)
    if not html:
        print("❌ 无法获取列表页")
        return
    
    # Extract matching links
    links = extract_article_links(html, SEARCH_KEYWORD)
    print(f"\n📋 找到 {len(links)} 篇匹配文章")
    
    new_count = 0
    skip_count = 0
    
    for i, link in enumerate(links, 1):
        print(f"\n[{i}/{len(links)}] {link['title'][:60]}...")
        print(f"  URL: {link['url']}")
        
        # Check if already exists
        if check_exists(link['url']):
            print(f"  ⏭️ 已存在，跳过")
            skip_count += 1
            continue
        
        # Fetch article
        article_html = fetch_page(link['url'])
        if not article_html:
            print(f"  ❌ 获取失败")
            continue
        
        article = extract_article_content(article_html)
        if not article['content']:
            print(f"  ⚠️ 内容为空，用标题替代")
            article['content'] = link['title']
        
        print(f"  📅 日期: {article['pub_date']}")
        print(f"  📝 内容: {len(article['content'])} 字符")
        
        # AI classification
        print(f"  🤖 AI分类中...")
        ai_result = classify_with_ai(article['title'] or link['title'], article['content'])
        print(f"  → 一级分类: {ai_result.get('level_1')}")
        print(f"  → 二级分类: {ai_result.get('level_2')}")
        print(f"  → 省份: {ai_result.get('provinces')}")
        print(f"  → 摘要: {ai_result.get('summary', '')[:50]}")
        
        # Build notice record
        notice = {
            'title': article['title'] or link['title'],
            'source_url': link['url'],
            'level_1': ai_result.get('level_1', '中央'),
            'level_2': ai_result.get('level_2', []),
            'pub_date': article.get('pub_date'),
            'content': article.get('content', ''),
            'summary': ai_result.get('summary', ''),
            'tags': ai_result.get('level_2', []),
            'provinces': ai_result.get('provinces', []),
            'case_count': ai_result.get('case_count', 0),
            'ai_processed': True
        }
        
        # Insert into database
        result = insert_notice(notice)
        if result:
            print(f"  ✅ 已入库")
            new_count += 1
        else:
            print(f"  ❌ 入库失败")
    
    print(f"\n{'='*50}")
    print(f"✅ 完成！新增 {new_count} 篇，跳过 {skip_count} 篇")
    
    # Show totals
    total = pg_execute("SELECT count(*) FROM notices", fetch=True)
    print(f"📊 数据库共 {total} 条通报")

if __name__ == "__main__":
    main()
