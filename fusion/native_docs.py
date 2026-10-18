"""The existing family OCR contract over the owned native Paperwork engine."""
import os
import time


class NativeDocs:
    def __init__(self, call, available):
        self.call, self.is_available = call, available

    def available(self, timeout=1.0):
        return self.is_available('paperwork')

    def _call(self, tool, arguments):
        try:
            result = self.call('paperwork', tool, arguments)
            if not isinstance(result, dict):
                return {'ok': False, 'via': 'kafka', 'kind': 'tool_error', 'error': 'Unexpected native OCR response'}
            failed = result.get('ok') is False or result.get('status') in ('error', 'failed', 'cancelled')
            return {**result, 'ok': not failed, 'via': 'kafka',
                    **({'kind': result.get('kind') or 'tool_error', 'error': str(result.get('error') or 'OCR failed')} if failed else {})}
        except Exception as exc:
            response = getattr(exc, 'response', None)
            status = getattr(response, 'status_code', None) or getattr(exc, 'status_code', None)
            error = str(exc)
            if response is not None:
                try:
                    detail = response.json()
                    error = str(detail.get('message') or detail.get('error') or detail.get('detail') or error)
                except (ValueError, AttributeError):
                    pass
            return {'ok': False, 'via': 'kafka', 'kind': 'app_down' if status == 409 else 'auth' if status == 401 else 'tool_error', 'error': error}

    def ocr_pdf(self, path, *, pages=None, dpi=200, lang='es', max_pages=40, timeout_s=900.0):
        try:
            deadline = time.monotonic() + max(1.0, float(timeout_s))
            args = {'path': os.path.abspath(os.fspath(path)), 'dpi': int(dpi), 'lang': lang, 'max_pages': int(max_pages),
                    'wait_s': max(0, min(150, float(timeout_s)))}
            if pages:
                args['pages'] = pages
            result = self._call('ocr_pdf', args)
            while result.get('ok') and result.get('status') in ('queued', 'running'):
                job_id = result.get('job_id')
                remaining = deadline - time.monotonic()
                if not job_id or remaining <= .5:
                    return {'ok': False, 'via': 'kafka', 'kind': 'timeout', 'error': f'still running after {timeout_s}s',
                            'job_id': job_id, 'still_running': True, 'result': result}
                polled = time.monotonic()
                result = self._call('ocr_status', {'job_id': job_id, 'wait_s': min(150, remaining)})
                if result.get('ok') and result.get('status') in ('queued', 'running') and time.monotonic()-polled < .2:
                    time.sleep(min(.25, max(0, deadline-time.monotonic())))
            return result
        except Exception as exc:
            return {'ok': False, 'via': 'kafka', 'kind': 'client_error', 'error': f'{type(exc).__name__}: {exc}'}

    def ocr_status(self, job_id='', wait_s=0, timeout_s=30.0):
        try:
            return self._call('ocr_status', {'job_id': job_id, 'wait_s': max(0, min(150, float(wait_s)))})
        except Exception as exc:
            return {'ok': False, 'via': 'kafka', 'kind': 'client_error', 'error': f'{type(exc).__name__}: {exc}'}
