#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
逐渠道直连探测：判断上游限流类型（长冷却惩罚 vs 分钟级 RPM/TPM 窗口）

用法：
  python probe_channels.py --db <one-api.db> --base-url https://token.sensenova.cn \
         --models kimi-k3,glm-5.2 --interval 5

  # 只测部分渠道、只看模型列表
  python probe_channels.py --db one-api.db --base-url https://token.sensenova.cn --channels 1,4,10 --list-models

注意：脚本只从库里取密钥并使用，不打印密钥明文。
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


def load_channels(db, base_url=None, ids=None):
    con = sqlite3.connect(db)
    cur = con.cursor()
    sql = "SELECT id,name,key,base_url,models,status FROM channels WHERE key<>''"
    args = []
    if ids:
        sql += " AND id IN (%s)" % ','.join('?' * len(ids))
        args += list(ids)
    if base_url:
        sql += " AND base_url LIKE ?"
        args.append('%' + base_url.split('//')[-1].split('/')[0] + '%')
    sql += " ORDER BY id"
    cur.execute(sql, args)
    rows = cur.fetchall()
    con.close()
    return rows


def http_post(url, key, body, timeout=45):
    data = json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method='POST')
    r.add_header('Authorization', 'Bearer ' + key)
    r.add_header('Content-Type', 'application/json')
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, '', round(time.time() - t0, 2)
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode('utf-8', 'replace'))
            err = j.get('error') or {}
            msg = '%s | %s' % (err.get('code', ''), (err.get('message') or '')[:50])
        except Exception:
            msg = 'unparsable body'
        return e.code, msg, round(time.time() - t0, 2)
    except Exception as e:
        return -1, repr(e)[:70], round(time.time() - t0, 2)


def http_get(url, key, timeout=30):
    r = urllib.request.Request(url, method='GET')
    r.add_header('Authorization', 'Bearer ' + key)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')
    except Exception as e:
        return -1, repr(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True, help='one-api.db 路径')
    ap.add_argument('--base-url', required=True, help='上游 base url，如 https://token.sensenova.cn')
    ap.add_argument('--models', default='', help='逗号分隔；留空则从渠道 models 字段推断')
    ap.add_argument('--channels', default='', help='逗号分隔的渠道 id，留空=全部')
    ap.add_argument('--interval', type=float, default=5.0, help='渠道之间等待秒数')
    ap.add_argument('--list-models', action='store_true', help='先打印上游 /v1/models')
    args = ap.parse_args()

    ids = [int(x) for x in args.channels.split(',') if x.strip()] or None
    chans = load_channels(args.db, args.base_url, ids)
    if not chans:
        print('未找到匹配渠道，检查 --db / --base-url / --channels')
        return 1
    print('匹配渠道 %d 个: %s' % (len(chans), [c[0] for c in chans]))

    base = args.base_url.rstrip('/')

    if args.list_models:
        st, txt = http_get(base + '/v1/models', chans[0][2])
        print('\n=== 上游 /v1/models (HTTP %s) ===' % st)
        try:
            for m in json.loads(txt).get('data', []):
                print('  -', m.get('id'))
        except Exception:
            print(txt[:600])

    models = [m.strip() for m in args.models.split(',') if m.strip()]
    if not models:
        models = [m.strip() for m in (chans[0][4] or '').split(',') if m.strip()][:3]
    print('\n探测模型: %s' % models)

    for model in models:
        print('\n===== %s =====' % model)
        ok = []
        errs = {}
        for cid, name, key, _bu, _mods, status in chans:
            st, msg, el = http_post(base + '/v1/chat/completions', key,
                                    {'model': model, 'messages': [{'role': 'user', 'content': 'hi'}],
                                     'max_tokens': 4})
            print('  [%s] ch%-4s %-14s chan_status=%-3s HTTP %-4s %-6ss %s'
                  % ('OK ' if st == 200 else 'ERR', cid, name, status, st, el, msg))
            if st == 200:
                ok.append(cid)
            else:
                errs[msg] = errs.get(msg, 0) + 1
            time.sleep(args.interval)
        print('  >>> 可用渠道 %s (%d/%d)' % (ok, len(ok), len(chans)))
        if errs:
            print('  >>> 错误归类: %s' % errs)
    return 0


if __name__ == '__main__':
    sys.exit(main())
