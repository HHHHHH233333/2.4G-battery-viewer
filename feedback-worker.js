// 2.4G 电量查看器 · 设备反馈接收端（Cloudflare Worker）
//
// 部署后，把返回的 https://xxx.workers.dev 填进 battery_viewer.py 的
// FEEDBACK_ENDPOINT，重新打包即可实现真正的“一键上传”。
//
// 需要的 Secret（在 Cloudflare 后台 Settings -> Variables 里配）：
//   GH_TOKEN : 一个 GitHub fine-grained token，只给 my-repository 的 Issues: Write
//   GH_REPO  : 可选，默认 HHHHHH233333/my-repository
//
// 安全：这里只是转发。报告内容在客户端已经生成并展示，用户点“发送”才 POST 过来；
// 本 Worker 不保存任何日志、不落盘、不读取键盘内容。

export default {
  async fetch(request, env) {
    if (request.method !== 'POST') {
      return new Response('POST only', { status: 405 });
    }
    const token = env.GH_TOKEN;
    if (!token) {
      return json({ ok: false, error: 'worker 未配置 GH_TOKEN' }, 500);
    }
    const repo = env.GH_REPO || 'HHHHHH233333/my-repository';

    let body;
    try {
      body = await request.json();
    } catch (e) {
      return json({ ok: false, error: 'invalid json' }, 400);
    }

    const app = body.app || '';
    const version = body.version || '';
    const time = body.time || '';
    const text = body.text || '';
    const descriptorHex = body.descriptor_hex || '';
    if (!text) {
      return json({ ok: false, error: 'missing text' }, 400);
    }

    const title = '设备适配请求' + (app ? ' · ' + app : '');
    const parts = [
      '<!-- 由 2.4G 电量查看器 自动提交 -->',
      '**app**: ' + (app || '?'),
      '**version**: ' + (version || '?'),
      '**time**: ' + (time || '?'),
      '',
      '<pre>',
      text,
      '</pre>',
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

    const gh = await fetch('https://api.github.com/repos/' + repo + '/issues', {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'battery-viewer-feedback',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ title: title, body: issueBody }),
    });
    const data = await gh.json().catch(function () { return {}; });
    if (!gh.ok) {
      return json({ ok: false, error: String(data.message || gh.status) }, 502);
    }
    return json({ ok: true, url: data.html_url }, 200);
  },
};

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status,
    headers: { 'Content-Type': 'application/json' },
  });
}
