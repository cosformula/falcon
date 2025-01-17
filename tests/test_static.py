# -*- coding: utf-8 -*-

import io
import os

import pytest

import falcon
from falcon.routing import StaticRoute, StaticRouteAsync
import falcon.testing as testing

import _util  # NOQA


@pytest.fixture()
def client(asgi):
    app = _util.create_app(asgi=asgi)
    client = testing.TestClient(app)
    client.asgi = asgi
    return client


def create_sr(asgi, *args, **kwargs):
    sr_type = StaticRouteAsync if asgi else StaticRoute
    return sr_type(*args, **kwargs)


@pytest.mark.parametrize(
    'uri',
    [
        # Root
        '/static',
        '/static/',
        '/static/.',
        # Attempt to jump out of the directory
        '/static/..',
        '/static/../.',
        '/static/.././etc/passwd',
        '/static/../etc/passwd',
        '/static/css/../../secret',
        '/static/css/../../etc/passwd',
        '/static/./../etc/passwd',
        # The file system probably won't process escapes, but better safe than sorry
        '/static/css/../.\\056/etc/passwd',
        '/static/./\\056./etc/passwd',
        '/static/\\056\\056/etc/passwd',
        # Double slash
        '/static//test.css',
        '/static//COM10',
        '/static/path//test.css',
        '/static/path///test.css',
        '/static/path////test.css',
        '/static/path/foo//test.css',
        # Control characters (0x00–0x1f and 0x80–0x9f)
        '/static/.\x00ssh/authorized_keys',
        '/static/.\x1fssh/authorized_keys',
        '/static/.\x80ssh/authorized_keys',
        '/static/.\x9fssh/authorized_keys',
        # Reserved characters (~, ?, <, >, :, *, |, ', and ")
        '/static/~/.ssh/authorized_keys',
        '/static/.ssh/authorized_key?',
        '/static/.ssh/authorized_key>foo',
        '/static/.ssh/authorized_key|foo',
        '/static/.ssh/authorized_key<foo',
        '/static/something:something',
        '/static/thing*.sql',
        "/static/'thing'.sql",
        '/static/"thing".sql',
        # Trailing periods and spaces
        '/static/something.',
        '/static/something..',
        '/static/something ',
        '/static/ something ',
        '/static/ something ',
        '/static/something\t',
        '/static/\tsomething',
        # Too long
        '/static/' + ('t' * StaticRoute._MAX_NON_PREFIXED_LEN) + 'x',
        # Invalid unicode character
        '/static/\ufffdsomething',
    ],
)
def test_bad_path(asgi, uri, monkeypatch):
    monkeypatch.setattr(io, 'open', lambda path, mode: io.BytesIO())

    sr_type = StaticRouteAsync if asgi else StaticRoute
    sr = sr_type('/static', '/var/www/statics')

    req = _util.create_req(asgi, host='test.com', path=uri, root_path='statics')

    resp = _util.create_resp(asgi)

    with pytest.raises(falcon.HTTPNotFound):
        if asgi:
            falcon.async_to_sync(sr, req, resp)
        else:
            sr(req, resp)


@pytest.mark.parametrize(
    'prefix, directory',
    [
        ('static', '/var/www/statics'),
        ('/static', './var/www/statics'),
        ('/static', 'statics'),
        ('/static', '../statics'),
    ],
)
def test_invalid_args(client, prefix, directory):
    with pytest.raises(ValueError):
        create_sr(client.asgi, prefix, directory)

    with pytest.raises(ValueError):
        client.app.add_static_route(prefix, directory)


@pytest.mark.parametrize(
    'default',
    [
        'not-existing-file',
        # directories
        '.',
        '/tmp',
    ],
)
def test_invalid_args_fallback_filename(client, default):
    prefix, directory = '/static', '/var/www/statics'
    with pytest.raises(ValueError, match='fallback_filename'):
        create_sr(client.asgi, prefix, directory, fallback_filename=default)

    with pytest.raises(ValueError, match='fallback_filename'):
        client.app.add_static_route(prefix, directory, fallback_filename=default)


# NOTE(caselit) depending on the system configuration mime types
# can have alternative names
_MIME_ALTERNATIVE = {
    'application/zip': ('application/zip', 'application/x-zip-compressed')
}


@pytest.mark.parametrize(
    'uri_prefix, uri_path, expected_path, mtype',
    [
        ('/static/', '/css/test.css', '/css/test.css', 'text/css'),
        ('/static', '/css/test.css', '/css/test.css', 'text/css'),
        (
            '/static',
            '/' + ('t' * StaticRoute._MAX_NON_PREFIXED_LEN),
            '/' + ('t' * StaticRoute._MAX_NON_PREFIXED_LEN),
            'application/octet-stream',
        ),
        ('/static', '/.test.css', '/.test.css', 'text/css'),
        ('/some/download/', '/report.pdf', '/report.pdf', 'application/pdf'),
        (
            '/some/download/',
            '/Fancy Report.pdf',
            '/Fancy Report.pdf',
            'application/pdf',
        ),
        ('/some/download', '/report.zip', '/report.zip', 'application/zip'),
        ('/some/download', '/foo/../report.zip', '/report.zip', 'application/zip'),
        (
            '/some/download',
            '/foo/../bar/../report.zip',
            '/report.zip',
            'application/zip',
        ),
        (
            '/some/download',
            '/foo/bar/../../report.zip',
            '/report.zip',
            'application/zip',
        ),
    ],
)
def test_good_path(asgi, uri_prefix, uri_path, expected_path, mtype, monkeypatch):
    monkeypatch.setattr(io, 'open', lambda path, mode: io.BytesIO(path.encode()))

    sr = create_sr(asgi, uri_prefix, '/var/www/statics')

    req_path = uri_prefix[:-1] if uri_prefix.endswith('/') else uri_prefix
    req_path += uri_path

    req = _util.create_req(asgi, host='test.com', path=req_path, root_path='statics')

    resp = _util.create_resp(asgi)

    if asgi:

        async def run():
            await sr(req, resp)
            return await resp.stream.read()

        body = falcon.async_to_sync(run)
    else:
        sr(req, resp)
        body = resp.stream.read()

    assert resp.content_type in _MIME_ALTERNATIVE.get(mtype, (mtype,))
    assert body.decode() == os.path.normpath('/var/www/statics' + expected_path)


def test_lifo(client, monkeypatch):
    monkeypatch.setattr(io, 'open', lambda path, mode: io.BytesIO(path.encode()))

    client.app.add_static_route('/downloads', '/opt/somesite/downloads')
    client.app.add_static_route('/downloads/archive', '/opt/somesite/x')

    response = client.simulate_request(path='/downloads/thing.zip')
    assert response.status == falcon.HTTP_200
    assert response.text == os.path.normpath('/opt/somesite/downloads/thing.zip')

    response = client.simulate_request(path='/downloads/archive/thingtoo.zip')
    assert response.status == falcon.HTTP_200
    assert response.text == os.path.normpath('/opt/somesite/x/thingtoo.zip')


def test_lifo_negative(client, monkeypatch):
    monkeypatch.setattr(io, 'open', lambda path, mode: io.BytesIO(path.encode()))

    client.app.add_static_route('/downloads/archive', '/opt/somesite/x')
    client.app.add_static_route('/downloads', '/opt/somesite/downloads')

    response = client.simulate_request(path='/downloads/thing.zip')
    assert response.status == falcon.HTTP_200
    assert response.text == os.path.normpath('/opt/somesite/downloads/thing.zip')

    response = client.simulate_request(path='/downloads/archive/thingtoo.zip')
    assert response.status == falcon.HTTP_200
    assert response.text == os.path.normpath(
        '/opt/somesite/downloads/archive/thingtoo.zip'
    )


def test_downloadable(client, monkeypatch):
    monkeypatch.setattr(io, 'open', lambda path, mode: io.BytesIO(path.encode()))

    client.app.add_static_route(
        '/downloads', '/opt/somesite/downloads', downloadable=True
    )
    client.app.add_static_route('/assets/', '/opt/somesite/assets')

    response = client.simulate_request(path='/downloads/thing.zip')
    assert response.status == falcon.HTTP_200
    assert response.headers['Content-Disposition'] == 'attachment; filename="thing.zip"'

    response = client.simulate_request(path='/downloads/Some Report.zip')
    assert response.status == falcon.HTTP_200
    assert (
        response.headers['Content-Disposition']
        == 'attachment; filename="Some Report.zip"'
    )

    response = client.simulate_request(path='/assets/css/main.css')
    assert response.status == falcon.HTTP_200
    assert 'Content-Disposition' not in response.headers


def test_downloadable_not_found(client):
    client.app.add_static_route(
        '/downloads', '/opt/somesite/downloads', downloadable=True
    )

    response = client.simulate_request(path='/downloads/thing.zip')
    assert response.status == falcon.HTTP_404


@pytest.mark.parametrize(
    'uri, default, expected, content_type',
    [
        ('', 'default', 'default', 'application/octet-stream'),
        ('other', 'default.html', 'default.html', 'text/html'),
        ('zip', 'default.zip', 'default.zip', 'application/zip'),
        ('index2', 'index', 'index2', 'application/octet-stream'),
        ('absolute', '/foo/bar/index', '/foo/bar/index', 'application/octet-stream'),
        ('docs/notes/test.txt', 'index.html', 'index.html', 'text/html'),
        (
            'index.html_files/test.txt',
            'index.html',
            'index.html_files/test.txt',
            'text/plain',
        ),
    ],
)
@pytest.mark.parametrize('downloadable', [True, False])
def test_fallback_filename(
    asgi, uri, default, expected, content_type, downloadable, monkeypatch
):
    def mock_open(path, mode):
        if os.path.normpath(default) in path:
            return io.BytesIO(path.encode())

        raise IOError()

    monkeypatch.setattr(io, 'open', mock_open)
    monkeypatch.setattr(
        'os.path.isfile', lambda file: os.path.normpath(default) in file
    )

    sr = create_sr(
        asgi,
        '/static',
        '/var/www/statics',
        downloadable=downloadable,
        fallback_filename=default,
    )

    req_path = '/static/' + uri

    req = _util.create_req(asgi, host='test.com', path=req_path, root_path='statics')
    resp = _util.create_resp(asgi)

    if asgi:

        async def run():
            await sr(req, resp)
            return await resp.stream.read()

        body = falcon.async_to_sync(run)
    else:
        sr(req, resp)
        body = resp.stream.read()

    assert sr.match(req.path)
    assert body.decode() == os.path.normpath(os.path.join('/var/www/statics', expected))
    assert resp.content_type in _MIME_ALTERNATIVE.get(content_type, (content_type,))

    if downloadable:
        assert os.path.basename(expected) in resp.downloadable_as
    else:
        assert resp.downloadable_as is None


@pytest.mark.parametrize('strip_slash', [True, False])
@pytest.mark.parametrize(
    'path, fallback, static_exp, assert_axp',
    [
        ('/index', 'index.html', 'index', 'index'),
        ('', 'index.html', 'index.html', None),
        ('/', 'index.html', 'index.html', None),
        ('/other', 'index.html', 'index.html', None),
        ('/other', 'index.raise', None, None),
    ],
)
def test_e2e_fallback_filename(
    client, monkeypatch, strip_slash, path, fallback, static_exp, assert_axp
):
    def mockOpen(path, mode):
        if 'index' in path and 'raise' not in path:
            return io.BytesIO(path.encode())
        raise IOError()

    monkeypatch.setattr(io, 'open', mockOpen)
    monkeypatch.setattr('os.path.isfile', lambda file: 'index' in file)

    client.app.req_options.strip_url_path_trailing_slash = strip_slash
    client.app.add_static_route(
        '/static', '/opt/somesite/static', fallback_filename=fallback
    )
    client.app.add_static_route('/assets/', '/opt/somesite/assets')

    def test(prefix, directory, expected):
        response = client.simulate_request(path=prefix + path)
        if expected is None:
            assert response.status == falcon.HTTP_404
        else:
            assert response.status == falcon.HTTP_200
            assert response.text == os.path.normpath(directory + expected)

    test('/static', '/opt/somesite/static/', static_exp)
    test('/assets', '/opt/somesite/assets/', assert_axp)


@pytest.mark.parametrize(
    'default, path, expected',
    [
        (None, '/static', False),
        (None, '/static/', True),
        (None, '/staticfoo', False),
        (None, '/static/foo', True),
        ('index2', '/static', True),
        ('index2', '/static/', True),
        ('index2', '/staticfoo', False),
        ('index2', '/static/foo', True),
    ],
)
def test_match(asgi, default, path, expected, monkeypatch):
    monkeypatch.setattr('os.path.isfile', lambda file: True)
    sr = create_sr(asgi, '/static', '/var/www/statics', fallback_filename=default)

    assert sr.match(path) == expected


def test_filesystem_traversal_fuse(client, monkeypatch):
    def suspicious_normpath(path):
        return 'assets/../../../../' + path

    client.app.add_static_route('/static', '/etc/nginx/includes/static-data')
    monkeypatch.setattr('os.path.normpath', suspicious_normpath)
    response = client.simulate_request(path='/static/shadow')
    assert response.status == falcon.HTTP_404
