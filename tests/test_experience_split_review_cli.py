"""Entry point parser and lazy-import checks for split review."""

import os
import subprocess
import sys


def run_cli(*args):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(("src", env.get("PYTHONPATH", "")))
    return subprocess.run(
        [sys.executable, "-m", "memecoin_experience", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_help_does_not_import_storage_or_model_stack():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import memecoin_experience.__main__; assert not any(x in sys.modules for x in ('memecoin_experience.storage','memecoin_experience.learning','torch','numpy'))"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    help_result = run_cli("split-review", "--help")
    assert help_result.returncode == 0
    assert "--input" in help_result.stdout
    assert "--output" in help_result.stdout


def test_cli_rejects_duplicate_keys_before_loading_helpers(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"snapshot":{},"snapshot":{}}')
    result = run_cli("split-review", "--input", str(path))
    assert result.returncode != 0
    assert "duplicate_json_key" in result.stderr


def test_cli_rejects_nonfinite_json_before_loading_helpers(tmp_path):
    path = tmp_path / "nonfinite.json"
    path.write_text('{"snapshot":NaN}')
    result = run_cli("split-review", "--input", str(path))
    assert result.returncode != 0
    assert "nonfinite_json_constant" in result.stderr


def test_real_cli_stdout_file_and_runtime_import_boundary(tmp_path):
    import json
    from test_experience_split_review import synthetic_request
    from memecoin_experience.split_review import analyze

    payload = synthetic_request()
    source = tmp_path / "request.json"
    target = tmp_path / "review.json"
    source.write_text(json.dumps(payload))
    expected = analyze(payload)
    stdout = run_cli("split-review", "--input", str(source))
    assert stdout.returncode == 0, stdout.stderr
    assert json.loads(stdout.stdout) == expected
    output = run_cli("split-review", "--input", str(source), "--output", str(target))
    assert output.returncode == 0, output.stderr
    assert output.stdout == ""
    assert target.read_text() == stdout.stdout
    code = '''
import importlib.abc, runpy, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('memecoin_experience.storage', 'memecoin_experience.sources', 'memecoin_experience.learning', 'memecoin_research_storage', 'memecoin_training', 'torch', 'numpy')):
            raise AssertionError('forbidden import: ' + fullname)
sys.meta_path.insert(0, Guard())
sys.argv = ['memecoin_experience', 'split-review', '--input', sys.argv[1]]
runpy.run_module('memecoin_experience', run_name='__main__')
'''
    guarded = subprocess.run([sys.executable, "-c", code, str(source)], capture_output=True, text=True, check=False)
    assert guarded.returncode == 0, guarded.stderr
    assert json.loads(guarded.stdout) == expected
