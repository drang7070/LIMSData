"""Standalone, authenticated LIMS web application; no MES dependencies."""
import hmac
import csv
import io
import os
from contextlib import closing
from urllib.parse import urlparse
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from bootstrap import bootstrap
from lims import core


def create_app():
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=4096)
    username = os.environ.get('LIMS_WEB_USERNAME','')
    password = os.environ.get('LIMS_WEB_PASSWORD','')
    production = os.environ.get('LIMS_ENV','local') == 'production'
    if production and (not username or len(password) < 16):
        raise RuntimeError('生产环境需要LIMS_WEB_USERNAME及至少16字符的LIMS_WEB_PASSWORD')
    bootstrap()

    @app.before_request
    def authenticate():
        if request.path == '/healthz':
            return None
        if username or password:
            auth = request.authorization
            if not auth or not hmac.compare_digest((auth.username or '').encode(),username.encode()) or not hmac.compare_digest((auth.password or '').encode(),password.encode()):
                return Response('需要登录',401,{'WWW-Authenticate':'Basic realm="LIMSData", charset="UTF-8"'})

    @app.after_request
    def protect_response(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        return response

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.get('/')
    @app.get('/lims')
    def index():
        return render_template('index.html')

    @app.get('/api/lims/status')
    def status():
        return jsonify(core.status())

    @app.get('/api/lims/options')
    def options():
        return jsonify(core.options(request.args.to_dict(flat=False)))

    @app.get('/api/lims/query')
    def query():
        return jsonify(core.query(request.args.to_dict(flat=False)))

    @app.post('/api/lims/sync')
    def sync():
        if os.environ.get('LIMS_ENABLE_SYNC','false').lower() != 'true':
            return jsonify(error='当前部署为本地数据查询模式，未开启源库采集'),403
        origin = request.headers.get('Origin')
        if origin and urlparse(origin).netloc != request.host:
            return jsonify(error='跨站采集请求被拒绝'),403
        payload = request.get_json()
        return jsonify(core.start_sync(payload.get('days',140),payload.get('date_basis','DATEENTER'))),202

    @app.get('/api/lims/export')
    def export():
        where, args = core.conditions(request.args.to_dict(flat=False))
        columns = ['sampled_at','plant','sample','analyte','analyte_raw','result_raw','value','unit','sample_type','qualifier','entered_at']
        def generate():
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            yield '\ufeff'
            writer.writerow(columns)
            yield buffer.getvalue()
            buffer.seek(0); buffer.truncate(0)
            with closing(core.connect()) as db:
                cursor = db.execute(f'SELECT {",".join(columns)} FROM results WHERE {where} ORDER BY sampled_at,id',args)
                for row in cursor:
                    values = [('\''+v if isinstance(v,str) and v[:1] in ('=','+','-','@') else v) for v in row]
                    writer.writerow(values)
                    if buffer.tell() >= 65536:
                        yield buffer.getvalue()
                        buffer.seek(0); buffer.truncate(0)
                if buffer.tell():
                    yield buffer.getvalue()
        return Response(stream_with_context(generate()),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="LIMS-results.csv"'})

    @app.errorhandler(ValueError)
    @app.errorhandler(TypeError)
    def invalid_request(exc):
        return jsonify(error='请求参数无效：'+str(exc)),400

    return app


if __name__ == '__main__':
    from waitress import serve
    host = os.environ.get('LIMS_HOST','127.0.0.1')
    if host not in ('127.0.0.1','localhost','::1') and os.environ.get('LIMS_ENV') != 'production':
        raise RuntimeError('对外监听需要LIMS_ENV=production及访问账号密码')
    serve(create_app(), host=host, port=int(os.environ.get('PORT','8080')), threads=4)
