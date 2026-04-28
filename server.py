#!/usr/bin/env python3
"""
Notices Database API Server
Serves the government notice data with search, filter, and AI query.
"""

import json
import subprocess
import os
import http.server
import urllib.parse

SUDO_ASKPASS = "/tmp/ssh_pass_ca.sh"
PG_CONTAINER = "1Panel-postgresql-qRXy"
PG_USER = "user_EjB5yH"
PG_DB = "notices"
OLLAMA_URL = "http://192.168.123.33:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:9b"

def get_pg_password():
    env = {"SUDO_ASKPASS": SUDO_ASKPASS, "DISPLAY": "none"}
    r = subprocess.run(f"sudo -A docker exec {PG_CONTAINER} printenv POSTGRES_PASSWORD",
                      shell=True, capture_output=True, text=True, timeout=10, env=env)
    return r.stdout.strip()

PG_PASSWORD = get_pg_password()

def pg(sql, fetch=True):
    env = {"SUDO_ASKPASS": SUDO_ASKPASS, "DISPLAY": "none"}
    fmt = "-t -A -F'|'" if fetch else ""
    sql_escaped = sql.replace('"', '\\"')
    cmd = f"sudo -A docker exec -e PGPASSWORD='{PG_PASSWORD}' {PG_CONTAINER} psql -U {PG_USER} -h localhost -d {PG_DB} {fmt} -c \"{sql_escaped}\""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, env=env)
    return r.stdout.strip() if r.returncode == 0 else ""

def query_notices(search="", level1=None, level2=None, limit=50, offset=0):
    """Query notices with filters"""
    where = ["1=1"]
    
    if search:
        escaped = search.replace("'", "''")
        where.append(f"(title ILIKE '%{escaped}%' OR content ILIKE '%{escaped}%' OR summary ILIKE '%{escaped}%')")
    
    if level1:
        levels = [f"'{l.replace(chr(39), chr(39)+chr(39))}'" for l in level1]
        where.append(f"level_1 IN ({','.join(levels)})")
    
    if level2:
        tags = [f"'{t.replace(chr(39), chr(39)+chr(39))}'" for t in level2]
        where.append(f"level_2 && ARRAY[{','.join(tags)}]")
    
    where_clause = " AND ".join(where)
    
    sql = f"""SELECT id, title, source_url, level_1, array_to_string(level_2,','), 
              publish_date, summary, array_to_string(provinces,','), case_count
              FROM notices WHERE {where_clause}
              ORDER BY publish_date DESC NULLS LAST
              LIMIT {limit} OFFSET {offset}"""
    
    rows = pg(sql)
    results = []
    if rows:
        for row in rows.split('\n'):
            if '|' in row:
                parts = row.split('|')
                if len(parts) >= 9:
                    results.append({
                        'id': int(parts[0]) if parts[0].isdigit() else 0,
                        'title': parts[1],
                        'url': parts[2],
                        'level1': parts[3],
                        'level2': parts[4].split(',') if parts[4] else [],
                        'date': parts[5] if parts[5] else None,
                        'summary': parts[6],
                        'provinces': parts[7].split(',') if parts[7] else [],
                        'caseCount': int(parts[8]) if parts[8].isdigit() else 0
                    })
    
    # Get total count
    count_sql = f"SELECT count(*) FROM notices WHERE {where_clause}"
    total = pg(count_sql).strip()
    total = int(total) if total.isdigit() else 0
    
    return results, total

def get_stats():
    """Get database statistics"""
    total = pg("SELECT count(*) FROM notices").strip()
    provinces = pg("SELECT count(DISTINCT unnest(provinces)) FROM notices").strip()
    level2 = pg("SELECT array_to_string(array_agg(DISTINCT unnest), ',') FROM (SELECT unnest(level_2) FROM notices) t").strip()
    level1_list = pg("SELECT array_to_string(array_agg(DISTINCT level_1), ',') FROM notices WHERE level_1 IS NOT NULL").strip()
    
    return {
        'total': int(total) if total.isdigit() else 0,
        'provinces': int(provinces) if provinces.isdigit() else 0,
        'categories': level2.split(',') if level2 else [],
        'level1List': level1_list.split(',') if level1_list else []
    }

def ai_query(question):
    """Use Ollama to translate natural language to SQL and query"""
    # Get table schema for context
    schema = """表 notices 字段:
id(int), title(text), source_url(text), level_1(varchar-中央/省名), level_2(text[]-问题类型),
publish_date(date), content(text), summary(text), tags(text[]), provinces(text[]),
case_count(int), scraped_at(timestamp), ai_processed(bool)
二级分类包括: 形式主义, 官僚主义, 层层加码, 数据造假, 考核泛滥, 脱离实际, 资源浪费, 强制摊派"""
    
    prompt = f"""你是一个SQL查询助手。根据用户的自然语言问题，生成PostgreSQL查询语句。
{schema}

用户问题：{question}

只返回SQL语句（不要SELECT，不要解释），格式：
SELECT id, title, source_url, level_1, array_to_string(level_2,','), publish_date, summary, array_to_string(provinces,',')
FROM notices WHERE ... ORDER BY publish_date DESC LIMIT 20"""

    try:
        r = subprocess.run(
            ['curl', '-s', '-m', '30', OLLAMA_URL,
             '-H', 'Content-Type: application/json',
             '-d', json.dumps({"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0}})],
            capture_output=True, text=True, timeout=60
        )
        data = json.loads(r.stdout)
        response = data.get('response', '')
        
        # Extract SQL
        sql_match = re.search(r'SELECT\s+.+?FROM\s+notices.+?(?:ORDER BY.+?)?(?:LIMIT\s+\d+)?', response, re.DOTALL | re.IGNORECASE)
        if sql_match:
            sql = sql_match.group(0).strip()
            if 'LIMIT' not in sql.upper():
                sql += ' LIMIT 20'
            
            rows = pg(sql)
            results = []
            if rows:
                for row in rows.split('\n'):
                    if '|' in row:
                        parts = row.split('|')
                        if len(parts) >= 8:
                            results.append({
                                'id': int(parts[0]) if parts[0].isdigit() else 0,
                                'title': parts[1],
                                'url': parts[2],
                                'level1': parts[3],
                                'level2': parts[4].split(',') if parts[4] else [],
                                'date': parts[5] if parts[5] else None,
                                'summary': parts[6],
                                'provinces': parts[7].split(',') if parts[7] else []
                            })
            
            return {'sql': sql, 'results': results, 'count': len(results)}
        else:
            # Fallback: just search in content
            escaped = question.replace("'", "''")
            results, total = query_notices(search=question)
            return {'sql': f'fallback search: {question}', 'results': results, 'count': total}
    except Exception as e:
        return {'error': str(e), 'results': [], 'count': 0}

import re

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        if path == '/' or path == '/index.html':
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'rb') as f:
                self.wfile.write(f.read())
            return
        elif path == '/api/stats':
            data = get_stats()
        elif path == '/api/notices':
            search = params.get('search', [''])[0]
            level1 = params.get('level1', None)
            level2 = params.get('level2', None)
            offset = int(params.get('offset', ['0'])[0])
            results, total = query_notices(search, level1, level2, offset=offset)
            data = {'results': results, 'total': total}
        elif path == '/api/ai':
            question = params.get('q', [''])[0]
            data = ai_query(question)
        else:
            data = {'error': 'not found'}
        
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    
    def log_message(self, format, *args):
        pass  # Suppress logs

if __name__ == '__main__':
    port = 3008
    server = http.server.HTTPServer(('0.0.0.0', port), Handler)
    print(f"🚀 Notices API running on http://0.0.0.0:{port}")
    server.serve_forever()
