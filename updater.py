# -*- coding: utf-8 -*-
"""
DSHSkin · 更新检查（P2-10，克制方案）
====================================
只做「检查 + 引导」，**不自动替换运行中的 exe**：
- Windows 下运行中的可执行文件被锁定，且静默自替换有安全风险；
- 因此本模块只查询 GitHub Release 的最新版本、与本地版本比对、返回下载页/资产，
  由用户自行下载替换。仓库地址通过常量或环境变量 DSH_SKIN_REPO（owner/repo）配置，
  未配置时返回 configured=False（面板不显示更新提示）。
"""
import json
import os
import re
import urllib.request

# 发布后把这里改成你的 GitHub 仓库（owner/repo）；也可用环境变量临时覆盖。
UPDATER_REPO = ''
API_TMPL = 'https://api.github.com/repos/{repo}/releases/latest'
TIMEOUT = 8


def version_tuple(v):
    nums = re.findall(r'\d+', str(v or '').lstrip('vV'))
    t = tuple(int(x) for x in nums[:3])
    return t + (0,) * (3 - len(t)) if len(t) < 3 else t


def configured_repo():
    return (os.environ.get('DSH_SKIN_REPO') or UPDATER_REPO or '').strip().strip('/')


def check_for_update(current_version, repo=None, timeout=TIMEOUT):
    """返回检查结果 dict。网络/未配置均不抛异常，返回 ok=False + reason。"""
    repo = repo or configured_repo()
    base = {'configured': bool(repo), 'current': current_version,
            'latest': '', 'has_update': False, 'url': '', 'notes': '', 'assets': []}
    if not repo:
        base['ok'] = False
        base['reason'] = '未配置更新源（设置 DSH_SKIN_REPO=owner/repo 或 updater.UPDATER_REPO）'
        return base
    url = API_TMPL.format(repo=repo)
    try:
        req = urllib.request.Request(url, headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'DSHSkin-updater',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
    except Exception as e:
        base['ok'] = False
        base['reason'] = '检查更新失败: {0}'.format(e)
        return base
    latest = (data.get('tag_name') or '').strip()
    base.update({
        'ok': True,
        'latest': latest,
        'url': data.get('html_url', ''),
        'notes': (data.get('body') or '')[:4000],
        'assets': [{'name': a.get('name'), 'url': a.get('browser_download_url'),
                    'size': a.get('size')} for a in data.get('assets', [])],
        'prerelease': bool(data.get('prerelease')),
    })
    if latest:
        base['has_update'] = version_tuple(latest) > version_tuple(current_version)
    return base
