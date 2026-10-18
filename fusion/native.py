"""Native factories are imported only after an explicit module activation."""
import os
from borges.hoard_link.guard import check_request
from borges.hoard_link.copilot import install_copilot
from .specs import SPECS


def factory(key, data_dir, *, port, automatic=False, native_call=None, native_available=None):
    if key == 'library':
        from borges.config import Config
        from borges.main import create_app
        from borges import links_source
        from borges.extract import pdf
        links_source.native_caller = lambda tool, args: native_call('links', tool, args)
        class NativeDocs:
            @staticmethod
            def available():
                return native_available('paperwork')
            @staticmethod
            def ocr_pdf(path, **kwargs):
                return native_call('paperwork', 'ocr_pdf', dict(path=path, **kwargs))
        pdf.fam_docs = NativeDocs
        config = Config.from_env()
        config.data_dir, config.port, config.data_dir_configured = data_dir, port, True
        config.watch = config.autostart = automatic
        config.defer_stale = not automatic
        return create_app(config)
    if key == 'paperwork':
        from kafka_hoard.config import Config, load_dotenv
        from kafka_hoard.main import create_app
        config = Config.from_env()
        config.data_dir, config.port, config.data_dir_configured = data_dir, port, True
        config.scheduler = automatic
        config.secrets.update(load_dotenv(data_dir.parent / '.env'))
        return create_app(config)
    from .links_engine import create_app
    return create_app(data_dir, port, automatic, native_call)
