import json, subprocess, urllib.request

def sql_out(sql):
    r = subprocess.run(
        ["docker", "exec", "anisignal-mysql-1", "sh", "-c",
         'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" anisignal -se "' + sql.replace('"', chr(92) + '"') + '"'],
        capture_output=True, text=True,
    )
    return r.stdout

cfg = json.loads(sql_out("SELECT value FROM site_settings WHERE `key`='ai';").strip().splitlines()[-1])
text = sql_out("SELECT cv.text FROM content_versions cv JOIN raw_contents rc ON rc.id=cv.raw_content_id WHERE rc.id=240 AND cv.version=(SELECT MAX(version) FROM content_versions WHERE raw_content_id=240);").strip()
prompt = open("/tmp/review_prompt.txt", encoding="utf-8").read()

payload = {
    "id": 240, "platform": "bilibili", "identity": "user",
    "title": "开箱秧秧鼠标垫&达妮娅鼠标&爱弥斯键盘",
    "text": text[:1200],
    "rule_category": None, "metrics": None,
    "matched_keywords": ["爱弥斯"],
    "known_terms": ["鸣潮", "爱弥斯"],
    "product_context": {"name": "爱弥斯联名机械键盘", "matched_terms": ["爱弥斯"]},
}
body = {
    "model": cfg["model"],
    "messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ],
    "temperature": 0.2,
    "response_format": {"type": "json_object"},
}
req = urllib.request.Request(
    "https://apihub.agnes-ai.com/v1/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["api_key"]},
)
resp = urllib.request.urlopen(req, timeout=120)
data = json.loads(resp.read())
print("RAW:", data["choices"][0]["message"]["content"][:700])
