from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import hashlib
import json
import os
import uuid
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# 青森市の市区町村コード
AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')
HAZARD_SPOTS_FILE = os.path.join(APP_DIR, 'data', 'hazard_spots.json')
HAZARD_UPLOAD_FOLDER = os.path.join(APP_DIR, 'static', 'uploads', 'hazards')
MAX_HAZARD_ATTACHMENTS = 5
MAX_HAZARD_ATTACHMENT_SIZE = 20 * 1024 * 1024
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
HAZARD_MEDIA_EXTENSIONS = {
    '.jpg': 'image', '.jpeg': 'image', '.png': 'image', '.gif': 'image', '.webp': 'image',
    '.mp4': 'video', '.webm': 'video', '.mov': 'video', '.m4v': 'video'
}
HAZARD_CATEGORIES = [
    '道路の損傷',
    '倒木・落下物',
    '浸水・冠水',
    '津波',
    '河川氾濫',
    '道路冠水',
    '土砂崩れ',
    '積雪による道路寸断',
    '獣害',
    'その他'
]

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])
hazard_spots = load_json(HAZARD_SPOTS_FILE, [])
reverse_geocode_cache = {}

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def save_shelters():
    """避難所データをファイルに保存する"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(shelters, f, ensure_ascii=False, indent=2)

def save_hazard_spots():
    """市民から投稿された危険箇所をファイルに保存する"""
    with open(HAZARD_SPOTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(hazard_spots, f, ensure_ascii=False, indent=2)

def render_hazard_report_error(message):
    return render_template(
        'shelter_register.html',
        categories=HAZARD_CATEGORIES,
        error=True,
        message=message,
        form_data=request.form
    )
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = [i for i in instructions if i.get('target') == '住民']
    return render_template('index.html', resident_notices=resident_notices)

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは危険箇所投稿ページ）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('hazard_report')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 市民が危険箇所を登録するページ（旧URLも互換用に維持）
@app.route('/hazard_report', methods=['GET', 'POST'])
@app.route('/shelter_register', methods=['GET', 'POST'])
def hazard_report():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        address = request.form.get('address', '').strip()
        category = request.form.get('category', '').strip()
        description = request.form.get('description', '').strip()
        try:
            latitude = float(request.form.get('latitude', ''))
            longitude = float(request.form.get('longitude', ''))
        except ValueError:
            latitude = longitude = None
        uploaded_files = [
            file for file in request.files.getlist('attachments')
            if file and file.filename
        ]

        if not title or len(title) > 80:
            message = '危険箇所の名称を80文字以内で入力してください。'
        elif len(address) > 250:
            message = '住所・地名は250文字以内で入力してください。'
        elif category not in HAZARD_CATEGORIES:
            message = '危険の種類を選択してください。'
        elif len(description) > 500:
            message = '状況は500文字以内で入力してください。'
        elif latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            message = '地図をクリックして場所を指定してください。'
        elif len(uploaded_files) > MAX_HAZARD_ATTACHMENTS:
            message = f'添付できるファイルは{MAX_HAZARD_ATTACHMENTS}件までです。'
        else:
            validated_files = []
            for file in uploaded_files:
                extension = os.path.splitext(file.filename)[1].lower()
                media_kind = HAZARD_MEDIA_EXTENSIONS.get(extension)
                content_type = (file.mimetype or '').lower()
                if not media_kind or not (content_type.startswith(media_kind + '/') or content_type == 'application/octet-stream'):
                    message = '画像（JPG、PNG、GIF、WebP）または動画（MP4、WebM、MOV）を選択してください。'
                    break
                file.stream.seek(0, os.SEEK_END)
                file_size = file.stream.tell()
                file.stream.seek(0)
                if file_size > MAX_HAZARD_ATTACHMENT_SIZE:
                    message = '1ファイルあたり20MB以下のものを選択してください。'
                    break
                validated_files.append((file, extension, media_kind, file_size))
            else:
                saved_attachments = []
                try:
                    os.makedirs(HAZARD_UPLOAD_FOLDER, exist_ok=True)
                    for file, extension, media_kind, file_size in validated_files:
                        stored_filename = uuid.uuid4().hex + extension
                        file.save(os.path.join(HAZARD_UPLOAD_FOLDER, stored_filename))
                        original_name = os.path.basename(file.filename.replace('\\', '/'))[:180]
                        saved_attachments.append({
                            'name': original_name,
                            'url': '/static/uploads/hazards/' + stored_filename,
                            'kind': media_kind,
                            'content_type': file.mimetype or 'application/octet-stream',
                            'size': file_size
                        })
                except OSError:
                    for attachment in saved_attachments:
                        stored_path = os.path.join(APP_DIR, 'static', attachment['url'].removeprefix('/static/'))
                        try:
                            os.remove(stored_path)
                        except OSError:
                            pass
                    message = 'ファイルを保存できませんでした。もう一度お試しください。'
                else:
                    spot_id = max((spot.get('id', 0) for spot in hazard_spots), default=0) + 1
                    hazard_spots.append({
                        'id': spot_id,
                        'title': title,
                        'address': address,
                        'category': category,
                        'description': description,
                        'latitude': round(latitude, 5),
                        'longitude': round(longitude, 5),
                        'attachments': saved_attachments,
                        'created_at': datetime.now(JST).isoformat(timespec='seconds')
                    })
                    save_hazard_spots()
                    return render_template(
                        'shelter_register.html',
                        categories=HAZARD_CATEGORIES,
                        success=True,
                        message='危険箇所を登録しました。'
                    )

        return render_hazard_report_error(message)

    return render_template('shelter_register.html', categories=HAZARD_CATEGORIES)

@app.errorhandler(413)
def handle_hazard_upload_too_large(error):
    return render_hazard_report_error('添付ファイルの合計サイズは50MB以下にしてください。'), 413

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template('shelter_search.html')

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=shelters)


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board')
@login_required
def board():
    resident_instructions = [i for i in instructions if i.get('target') == '住民']
    return render_template('board.html', instructions=resident_instructions)

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    results = filter_shelters(request.args.get('district'))
    return render_template('search_results.html', results=results)

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# JSON API：ホーム画面の危険箇所マーカー
@app.route('/api/hazard_spots', methods=['GET'])
def get_hazard_spots():
    public_spots = []
    for spot in hazard_spots:
        public_spot = {key: value for key, value in spot.items() if key != 'seen_by'}
        seen_by = spot.get('seen_by', [])
        public_spot['seen_count'] = len(seen_by) if isinstance(seen_by, list) else 0
        public_spots.append(public_spot)
    return jsonify(public_spots)

# 危険箇所の確認ボタン：同一ブラウザーからの重複を数えない
@app.route('/api/hazard_spots/<int:spot_id>/seen', methods=['POST'])
def mark_hazard_spot_seen(spot_id):
    payload = request.get_json(silent=True) or {}
    viewer_id = payload.get('viewer_id')
    if not isinstance(viewer_id, str) or not 16 <= len(viewer_id) <= 128:
        return jsonify({'error': 'Invalid viewer id'}), 400

    spot = next((item for item in hazard_spots if item.get('id') == spot_id), None)
    if spot is None:
        return jsonify({'error': 'Hazard spot not found'}), 404

    viewer_hash = hashlib.sha256(viewer_id.encode('utf-8')).hexdigest()
    seen_by = spot.setdefault('seen_by', [])
    if not isinstance(seen_by, list):
        seen_by = []
        spot['seen_by'] = seen_by

    already_seen = viewer_hash in seen_by
    if not already_seen:
        seen_by.append(viewer_hash)
        save_hazard_spots()

    return jsonify({
        'seen': True,
        'already_seen': already_seen,
        'count': len(seen_by)
    })

# 地図座標から住所・地名を取得する逆ジオコーディングAPI
@app.route('/api/reverse_geocode', methods=['GET'])
def reverse_geocode():
    try:
        latitude = float(request.args.get('lat', ''))
        longitude = float(request.args.get('lon', ''))
    except ValueError:
        return jsonify({'error': 'Invalid coordinates'}), 400

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return jsonify({'error': 'Coordinates are out of range'}), 400

    cache_key = (round(latitude, 5), round(longitude, 5))
    if cache_key in reverse_geocode_cache:
        return jsonify(reverse_geocode_cache[cache_key])

    lookup_url = (
        'https://nominatim.openstreetmap.org/reverse?format=jsonv2'
        f'&lat={latitude}&lon={longitude}&zoom=18&addressdetails=1&accept-language=ja'
    )
    lookup_request = urllib.request.Request(
        lookup_url,
        headers={'User-Agent': 'BousaiApp/1.0'}
    )
    try:
        with urllib.request.urlopen(lookup_request, timeout=5) as response:
            result = json.loads(response.read())
        location = {
            'name': result.get('name', ''),
            'address': result.get('display_name', '')
        }
        reverse_geocode_cache[cache_key] = location
        return jsonify(location)
    except Exception:
        return jsonify({'error': '住所候補を取得できませんでした'}), 502

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
