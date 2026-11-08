// 2.4G 电量查看器 · 设备反馈接收端（阿里云函数计算 FC，ES Module 版）
// 对应控制台的 index.mjs，入口 index.handler。
//
// 环境变量：
//   GH_TOKEN : 一个 classic GitHub token，只勾 repo
//   GH_REPO  : 可选，默认 HHHHHH233333/my-repository

import https from 'https';

function callGitHub(token, repo, payload) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(payload);
    const req = https.request({
      hostname: 'api.github.com',
      path: '/repos/' + repo + '/issues',
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'battery-viewer-feedback',
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(data)
      },
      timeout: 20000
    }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        let parsed = {};
        try { parsed = JSON.parse(body); } catch (e) {}
        resolve({ status: res.statusCode, data: parsed });
      });
    });
    req.on('error', (e) => reject(e));
    req.on('timeout', () => { req.destroy(new Error('github timeout')); });
    req.write(data);
    req.end();
  });
}

function resp(statusCode, obj) {
  return {
    isBase64Encoded: false,
    statusCode: statusCode,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify(obj)
  };
}

export const handler = async (event, context) => {
  // 阿里云 Web 函数：event 是 Buffer，内容是 JSON 格式的 HTTP 事件
  let raw = event;
  if (Buffer.isBuffer(event)) {
    raw = event.toString('utf-8');
  }
  let ev = {};
  try {
    ev = (typeof raw === 'string') ? JSON.parse(raw) : raw;
  } catch (e) {
    ev = {};
  }

  const rcHttp = (ev.requestContext && ev.requestContext.http) || {};
  const method = String(rcHttp.method || '').toUpperCase();

  if (method === 'GET') {
    return resp(200, { ok: true, service: '2.4G-battery-viewer feedback', hint: 'POST only' });
  }
  if (method !== 'POST') {
    return resp(405, { ok: false, error: 'POST only' });
  }

  const token = process.env.GH_TOKEN;
  if (!token) {
    return resp(500, { ok: false, error: '未配置 GH_TOKEN' });
  }
  const repo = process.env.GH_REPO || 'HHHHHH233333/my-repository';

  let bodyRaw = ev.body || '';
  if (ev.isBase64Encoded && bodyRaw) {
    bodyRaw = Buffer.from(bodyRaw, 'base64').toString('utf-8');
  }
  let body = {};
  try {
    body = (typeof bodyRaw === 'string') ? JSON.parse(bodyRaw) : bodyRaw;
  } catch (e) {
    return resp(400, { ok: false, error: 'invalid json' });
  }

  const text = body.text || '';
  if (!text) {
    return resp(400, { ok: false, error: 'missing text' });
  }

  const app = body.app || '';
  const version = body.version || '';
  const time = body.time || '';
  const descriptorHex = body.descriptor_hex || '';

  const title = '设备适配请求' + (app ? ' · ' + app : '');
  const parts = [
    '<!-- 由 2.4G 电量查看器 自动提交 -->',
    '**app**: ' + (app || '?'),
    '**version**: ' + (version || '?'),
    '**time**: ' + (time || '?'),
    '',
    '<pre>',
    text,
    '</pre>'
  ];
  if (descriptorHex) {
    parts.push('');
    parts.push('<details><summary>报告描述符（hex）</summary>');
    parts.push('');
    parts.push('<pre>');
    parts.push(descriptorHex);
    parts.push('</pre>');
    parts.push('');
    parts.push('</details>');
  }
  const issueBody = parts.join('\n');

  try {
    const res = await callGitHub(token, repo, { title: title, body: issueBody });
    if (res.status >= 200 && res.status < 300) {
      return resp(200, { ok: true, url: res.data.html_url });
    }
    return resp(502, { ok: false, error: String(res.data.message || res.status) });
  } catch (e) {
    return resp(502, { ok: false, error: String((e && e.message) || e) });
  }
};
