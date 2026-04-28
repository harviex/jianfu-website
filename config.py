# Notices Database Configuration
PG_HOST = "localhost"
PG_PORT = 5432
PG_USER = "user_EjB5yH"
PG_DB = "notices"

# Password retrieved from Docker env
import subprocess
def get_pg_password():
    r = subprocess.run(
        ["sudo", "-A", "docker", "exec", "1Panel-postgresql-qRXy", "printenv", "POSTGRES_PASSWORD"],
        capture_output=True, text=True, timeout=10,
        env={"SUDO_ASKPASS": "/tmp/ssh_pass_ca.sh", "DISPLAY": "none"}
    )
    return r.stdout.strip()

PG_PASSWORD = get_pg_password()

# Target sites
PEOPLE_CN_LIST = "http://zzxszy.people.cn/GB/458759/index.html"
SEARCH_KEYWORD = "中央层面整治形式主义为基层减负专项工作机制办公室"

# Ollama on CA
OLLAMA_URL = "http://192.168.123.33:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:9b"
