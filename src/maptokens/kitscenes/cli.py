"""Command line interface; all deployment-specific values are arguments."""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(prog='kitscenes')
    sub = p.add_subparsers(dest='command', required=True)
    plan = sub.add_parser('plan', help='Fetch manifest and make a balanced pinned plan')
    plan.add_argument('--state', required=True)
    plan.add_argument('--roots', nargs='+', required=True)
    plan.add_argument('--revision', default='main')
    plan.add_argument('--splits', nargs='*')
    plan.add_argument('--scenes', nargs='*')
    plan.add_argument('--cameras', nargs='*', default=['camera_ring_front'])
    plan.add_argument('--no-metadata', action='store_true')
    plan.add_argument('--include', action='append', default=[])
    plan.add_argument('--exclude', action='append', default=[])
    plan.add_argument('--limit', type=int)
    plan.add_argument('--mode', choices=['selective','full'], default='selective')
    run = sub.add_parser('run', help='Resume downloads from an existing plan')
    run.add_argument('--state', required=True)
    run.add_argument('--method', choices=['auto','python','curl','wget','hf'], default='auto')
    run.add_argument('--jobs', type=int, default=16)
    run.add_argument('--preview-scenes-per-split',type=int,default=2,
                     help='Full mode: download this many small train/val scenes first (0 disables)')
    bench = sub.add_parser('benchmark', help='Compare Python, curl and wget ranges on this server')
    bench.add_argument('--state', required=True)
    preview = sub.add_parser('prepare-preview', help='Prepare camera and map from a downloading full tar')
    preview.add_argument('--state',required=True)
    preview.add_argument('--scene',required=True)
    preview.add_argument('--tail-mib',type=int,default=32)
    serve = sub.add_parser('serve')
    serve.add_argument('--state', required=True)
    serve.add_argument('--host', default='0.0.0.0')
    serve.add_argument('--port', type=int, default=8795)
    args = p.parse_args()
    if args.command == 'plan':
        from .download import build_plan
        build_plan(args)
    elif args.command == 'benchmark':
        from .download import benchmark
        benchmark(args)
    elif args.command == 'run':
        from .download import Runner
        method = args.method
        full = json.loads((Path(args.state)/'plan.json').read_text())['mode'] == 'full'
        if method == 'auto':
            path = Path(args.state)/'benchmark.json'
            method = ('wget' if full else json.loads(path.read_text())['winner'] if path.exists() else 'python')
        if args.jobs < 1:
            p.error('--jobs must be positive')
        if args.preview_scenes_per_split < 0:
            p.error('--preview-scenes-per-split must be non-negative')
        if full:
            from .mirror import Mirror
            Mirror(args.state, 'wget' if method == 'python' else method,args.jobs,args.preview_scenes_per_split).run()
        else:
            if method == 'hf':
                p.error('hf supports full archives only')
            Runner(args.state,method,args.jobs).run()
    elif args.command == 'prepare-preview':
        from .preview import prepare_preview
        print(json.dumps(prepare_preview(args.state,args.scene,args.tail_mib),indent=2))
    elif args.command == 'serve':
        import uvicorn
        from .server import create_app
        uvicorn.run(create_app(args.state), host=args.host, port=args.port, access_log=False)


if __name__ == '__main__':
    main()
