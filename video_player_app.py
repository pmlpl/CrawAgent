"""
凤凰影院视频播放器 Web 应用
运行方式: python video_player_app.py
访问地址: http://localhost:5000
"""

from flask import Flask, render_template, request, jsonify
from crawagent.tools.video_parser import FengHuangVideoParser, parse_video
import json

app = Flask(__name__)
parser = FengHuangVideoParser()


@app.route('/')
def index():
    """主页"""
    return render_template('video_player.html')


@app.route('/search')
def search():
    """搜索视频
    
    参数:
        q: 搜索关键词
        page: 页码（默认1）
        page_size: 每页数量（默认20）
    """
    keyword = request.args.get('q', '')
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 20))
    
    if not keyword:
        return jsonify({'success': False, 'error': '请提供搜索关键词'})
    
    try:
        result = parser.search(keyword, page=page, page_size=page_size)
        return jsonify({
            'success': True,
            'keyword': keyword,
            'results': result['results'],
            'total': result['total'],
            'page': result['page'],
            'page_size': result['page_size'],
            'has_more': result['has_more']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/video/<video_id>')
def get_video_info(video_id):
    """获取视频详情和集数列表"""
    try:
        info = parser.get_video_info(video_id)
        return jsonify({
            'success': True,
            'title': info['title'],
            'video_id': video_id,
            'cover': info.get('cover', ''),
            'status': info.get('status', ''),
            'type': info.get('type', ''),
            'actors': info.get('actors', []),
            'director': info.get('director', ''),
            'year': info.get('year', ''),
            'intro': info.get('intro', ''),
            'is_variety': info.get('is_variety', False),
            'episodes': info['episodes'],
            'total_episodes': len(info['episodes'])
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/play')
def get_play_url():
    """获取播放 URL
    
    参数:
        url: 播放页面完整 URL
        或 video_id + some_id + episode
    """
    episode_url = request.args.get('url', '')
    video_id = request.args.get('video_id', '')
    some_id = request.args.get('some_id', '')
    episode = request.args.get('episode', '')
    
    try:
        if episode_url:
            result = parser.get_episode_url(episode_url)
        elif video_id and some_id and episode:
            result = parser.get_play_url(video_id, some_id, episode)
        else:
            return jsonify({'success': False, 'error': '参数不足'})
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/watch')
def watch_video():
    """直接观看视频（通过关键词搜索并播放第一集）
    
    参数:
        name: 视频名称（关键词）
        返回: 重定向到播放器页面，自动播放
    """
    name = request.args.get('name', '')
    if not name:
        return jsonify({'success': False, 'error': '请提供视频名称'})
    
    try:
        # 使用 parser.search() 搜索视频
        search_result = parser.search(name)
        search_results = search_result.get('results', [])
        
        if not search_results:
            return jsonify({'success': False, 'error': f'未找到视频: {name}'})
        
        # 取第一个搜索结果
        first_result = search_results[0]
        vid = first_result['video_id']
        title = first_result['title']
        
        # 获取视频信息
        info = parser.get_video_info(vid)
        if info['episodes']:
            first_ep = info['episodes'][0]
            # 获取播放 URL
            play_result = parser.get_play_url(vid, first_ep['some_id'], str(first_ep['episode']))
            if play_result.get('success') and play_result.get('url'):
                # 返回播放信息，前端会自动播放
                return jsonify({
                    'success': True,
                    'title': title,
                    'video_id': vid,
                    'cover': info.get('cover', ''),
                    'status': info.get('status', ''),
                    'type': info.get('type', ''),
                    'actors': info.get('actors', []),
                    'director': info.get('director', ''),
                    'year': info.get('year', ''),
                    'intro': info.get('intro', ''),
                    'is_variety': info.get('is_variety', False),
                    'play_url': play_result['url'],
                    'episode': first_ep['episode'],
                    'total_episodes': len(info['episodes']),
                    'episodes': info['episodes']
                })
        
        return jsonify({'success': False, 'error': f'未找到视频: {name}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/open_player')
def open_player():
    """打开播放器页面（供 agent 调用）
    返回 HTML 片段，包含打开播放器的指令
    """
    name = request.args.get('name', '')
    
    if name:
        # 返回带有视频名称的播放器页面
        return render_template('video_player.html', video_name=name)
    else:
        return render_template('video_player.html')


if __name__ == '__main__':
    print("=" * 50)
    print("凤凰影院视频播放器")
    print("访问地址: http://localhost:5000")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
