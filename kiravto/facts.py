"""Validate recorded LLM output before using it in buyer-facing text."""
import hashlib
import json
import sqlite3

def validate(description, response):
    try:
        parsed = json.loads(response) if isinstance(response,str) else response
        candidates = parsed.get('facts',[])
        if not isinstance(candidates,list):
            return [], 1
    except (ValueError,AttributeError):
        return [], 1
    accepted, rejected = [], 0
    for item in candidates:
        if not isinstance(item,dict):
            rejected += 1
            continue
        quote, fact = item.get('quote'), item.get('fact')
        if not isinstance(quote,str) or not quote.strip() or quote not in description or not isinstance(fact,str) or not fact.strip():
            rejected += 1
            continue
        if len(accepted) < 3:
            accepted.append({'fact': fact, 'quote': quote})
    return accepted, rejected

class FactCache:
    def __init__(self,path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS facts_cache(hash TEXT PRIMARY KEY, result TEXT NOT NULL, rejected INTEGER NOT NULL)')
    def key(self,text):
        return hashlib.sha256(text.encode()).hexdigest()
    def get(self,text):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT result,rejected FROM facts_cache WHERE hash=?',(self.key(text),)).fetchone()
            return (json.loads(row[0]),row[1]) if row else None
    def record(self,text,response):
        cached = self.get(text)
        if cached is not None:
            return cached
        result, rejected = validate(text,response)
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR IGNORE INTO facts_cache VALUES(?,?,?)',(self.key(text),json.dumps(result,ensure_ascii=False),rejected))
        return self.get(text)
