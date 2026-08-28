# Gunicorn 进程配置（与 Dockerfile / PM2 命令中的 -c gunicorn_config 配合）
#
# worker_exit：在 worker 进程结束前关闭 SQLite，减少解释器 shutdown 阶段与
# gthread 信号处理（sys.exit）交织时出现的 “Exception ignored in: threading” 类日志。
#
# 若仍偶发该类消息（Gunicorn issue #2918 等），可改用 sync worker：在命令行去掉 --threads，
# 或在此文件取消注释 worker_class = "sync"（并发降为每 worker 单请求）。


def worker_exit(server, worker) -> None:  # noqa: ARG001
    try:
        import simple_web_app

        simple_web_app.stop_gaokao_auto_backfill_scheduler()
        simple_web_app.stop_import_job_runner()
    except Exception:
        pass
    try:
        import auth_session_store

        auth_session_store.close_connection()
    except Exception:
        pass
    try:
        import user_store

        user_store.close_connection()
    except Exception:
        pass


def post_worker_init(worker) -> None:  # noqa: ARG001
    """Start the low-peak Gaokao backfill scheduler after each worker fork."""
    import simple_web_app

    simple_web_app.start_gaokao_auto_backfill_scheduler()
    simple_web_app.start_import_job_runner()
