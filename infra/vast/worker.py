# The PyWorker in front of sitecustomize.py's job server: /run starts a job,
# /status reads it. Both return at once, so long jobs never hold a request.
from vastai import BenchmarkConfig, HandlerConfig, LogActionConfig, Worker, WorkerConfig

Worker(WorkerConfig(
    model_server_url='http://127.0.0.1',
    model_server_port=18000,
    model_log_file='/var/log/vast-jobs.log',
    handlers=[
        HandlerConfig(route='/run', allow_parallel_requests=True, max_queue_time=60,
                      workload_calculator=lambda p: 100.0),
        HandlerConfig(route='/status', allow_parallel_requests=True, max_queue_time=60,
                      workload_calculator=lambda p: 1.0,
                      benchmark_config=BenchmarkConfig(dataset=[{'id': 'benchmark'}],
                                                       runs=2, concurrency=1)),
    ],
    log_action_config=LogActionConfig(on_load=['vast-jobs ready']),
)).run()
