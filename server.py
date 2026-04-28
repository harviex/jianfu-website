#!/usr/bin/env python3
"""
Notices Database API Server
Serves the government notice data with search, filter, and AI query.
Now includes feedback platform and admin features.
"""

import json
import subprocess
import os
import http.server
import urllib.parse
import re
from datetime import datetime

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

PG_PASSWORD = "***"

def pg(sql, fetch=True):
    env = {"SUDO_ASKPASS": SUDO_ASKPASS, "DISPLAY": "none"}
    fmt = "-t -A -F'|'" if fetch else ""
    sql_escaped = sql.replace('"', '\\"')
    cmd = f"sudo -A docker exec -e PGPASSWORD='***' {PG_CONTAINER} psql -U {PG_USER} -h localhost -d {PG_DB} {fmt} -c \"{sql_escaped}\""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, env=env)
    return r.stdout.strip() if r.returncode == 0 else ""

# ========== 通报数据相关函数 ==========

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
            escaped = question.replace("'", "''")
            results, total = query_notices(search=question)
            return {'sql': f'fallback search: {question}', 'results': results, 'count': total}
    except Exception as e:
        return {'error': str(e), 'results': [], 'count': 0}

# ========== 情况反映相关函数 ==========

# 福建省市县区数据
FUJIAN_REGIONS = {
    "福州市": ["鼓楼区", "台江区", "仓山区", "晋安区", "马尾区", "长乐区", "福清市", "闽侯县", "连江县", "罗源县", "闽清县", "永泰县"],
    "厦门市": ["思明区", "海沧区", "湖里区", "集美区", "同安区", "翔安区"],
    "漳州市": ["芗城区", "龙文区", "龙海区", "漳浦县", "云霄县", "诏安县", "东山县", "平和县", "南靖县", "长泰区", "华安县"],
    "泉州市": ["鲤城区", "丰泽区", "洛江区", "泉港区", "石狮市", "晋江市", "南安市", "惠安县", "安溪县", "永春县", "德化县", "金门县"],
    "三明市": ["三元区", "永安市", "明溪县", "清流县", "宁化县", "大田县", "尤溪县", "沙县区", "将乐县", "泰宁县", "建宁县"],
    "莆田市": ["城厢区", "涵江区", "荔城区", "秀屿区", "仙游县"],
    "南平市": ["延平区", "建阳区", "邵武市", "武夷山市", "建瓯市", "顺昌县", "浦城县", "光泽县", "松溪县", "政和县"],
    "龙岩市": ["新罗区", "永定区", "上杭县", "武平县", "长汀县", "连城县", "漳平市"],
    "宁德市": ["蕉城区", "福安市", "福鼎市", "霞浦县", "古田县", "屏南县", "寿宁县", "周宁县", "柘荣县"],
    "平潭综合实验区": ["平潭县"]
}

def submit_feedback(city, district, unit, description):
    """提交情况反映"""
    sql = f"""INSERT INTO feedback (city, district, unit, description, status, submitted_at)
              VALUES ('{city.replace(chr(39), chr(39)+chr(39))}', 
                      '{district.replace(chr(39), chr(39)+chr(39))}', 
                      '{unit.replace(chr(39), chr(39)+chr(39))}', 
                      '{description.replace(chr(39), chr(39)+chr(39))}', 
                      'pending', NOW())
              RETURNING id"""
    result = pg(sql)
    # 提取第一行（ID），忽略INSERT状态信息
    if result:
        first_line = result.strip().split('\n')[0].strip()
        if first_line.isdigit():
            return {'success': True, 'id': int(first_line)}
    return {'success': False, 'error': '提交失败'}

def get_feedback_list(status=None, limit=50, offset=0):
    """获取情况反映列表"""
    where = "1=1"
    if status:
        where += f" AND status = '{status}'"
    
    sql = f"""SELECT id, city, district, unit, left(description, 100), status, 
                     submitted_at, reviewed_at, review_comment
              FROM feedback WHERE {where}
              ORDER BY submitted_at DESC
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
                        'city': parts[1],
                        'district': parts[2],
                        'unit': parts[3],
                        'description': parts[4] + '...' if len(parts[4]) >= 100 else parts[4],
                        'status': parts[5],
                        'submitted_at': parts[6],
                        'reviewed_at': parts[7] if parts[7] else None,
                        'review_comment': parts[8] if parts[8] else None
                    })
    
    count_sql = f"SELECT count(*) FROM feedback WHERE {where}"
    total = pg(count_sql).strip()
    total = int(total) if total.isdigit() else 0
    
    return results, total

def update_feedback_status(feedback_id, status, comment=None):
    """更新情况反映状态（审核）"""
    comment_sql = f", review_comment = '{comment.replace(chr(39), chr(39)+chr(39))}'" if comment else ""
    sql = f"""UPDATE feedback 
              SET status = '{status}', reviewed_at = NOW(){comment_sql}
              WHERE id = {feedback_id}
              RETURNING id"""
    result = pg(sql)
    return {'success': bool(result), 'id': feedback_id}

def delete_feedback(feedback_id):
    """删除情况反映"""
    sql = f"DELETE FROM feedback WHERE id = {feedback_id} RETURNING id"
    result = pg(sql)
    return {'success': bool(result), 'id': feedback_id}

# ========== HTTP 请求处理 ==========

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        
        content_type = 'application/json; charset=utf-8'
        data = None
        
        if path == '/' or path == '/index.html':
            content_type = 'text/html; charset=utf-8'
        elif path == '/feedback.html':
            content_type = 'text/html; charset=utf-8'
        elif path == '/admin.html':
            content_type = 'text/html; charset=utf-8'
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
        elif path == '/api/feedback/list':
            status = params.get('status', [None])[0]
            offset = int(params.get('offset', ['0'])[0])
            results, total = get_feedback_list(status, offset=offset)
            data = {'results': results, 'total': total}
        elif path == '/api/regions':
            data = FUJIAN_REGIONS
        else:
            data = {'error': 'not found'}
        
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        if path in ['/', '/index.html']:
            with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'rb') as f:
                self.wfile.write(f.read())
        elif path == '/feedback.html':
            with open(os.path.join(os.path.dirname(__file__), 'feedback.html'), 'rb') as f:
                self.wfile.write(f.read())
        elif path == '/admin.html':
            with open(os.path.join(os.path.dirname(__file__), 'admin.html'), 'rb') as f:
                self.wfile.write(f.read())
        elif data is not None:
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        
        try:
            post_data = json.loads(body)
        except:
            post_data = {}
        
        data = None
        
        if path == '/api/feedback/submit':
            city = post_data.get('city', '')
            district = post_data.get('district', '')
            unit = post_data.get('unit', '')
            description = post_data.get('description', '')
            
            if not all([city, district, description]):
                data = {'success': False, 'error': '请填写完整信息'}
            else:
                data = submit_feedback(city, district, unit, description)
        
        elif path == '/api/feedback/review':
            feedback_id = post_data.get('id', 0)
            status = post_data.get('status', '')
            comment = post_data.get('comment', None)
            
            if feedback_id and status in ['approved', 'rejected']:
                data = update_feedback_status(feedback_id, status, comment)
            else:
                data = {'success': False, 'error': '参数错误'}
        
        elif path == '/api/feedback/delete':
            feedback_id = post_data.get('id', 0)
            if feedback_id:
                data = delete_feedback(feedback_id)
            else:
                data = {'success': False, 'error': '参数错误'}
        
        else:
            data = {'error': 'not found'}
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        if data is not None:
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    
    def log_message(self, format, *args):
        pass  # Suppress logs

if __name__ == '__main__':
    port = 3008
    server = http.server.HTTPServer(('0.0.0.0', port), Handler)
    print(f"🚀 Notices API running on http://0.0.0.0:{port}")
    server.serve_forever()
