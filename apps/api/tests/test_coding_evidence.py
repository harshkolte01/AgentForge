"""Objective qualification rejects model assertions and incomplete exit receipts."""
import unittest
from agentforge.contracts.coding import CodingTaskSpec, RepoSelection
from agentforge.tools.test_tools import qualification, observed_test_count
from pydantic import ValidationError

class EvidenceTests(unittest.TestCase):
    baseline = {"app.py": b"def add(a,b): return a-b\n", "test_app.py": b"# original assertions\n", "pyproject.toml": b"[tool.pytest.ini_options]\n"}
    def payload(self, **changes):
        return {"profile_id": "python_unittest", "exit_code": 0, "timed_out": False,
            "cleanup_confirmed": True, "output_truncated": False, "observed_test_count": 1, **changes}

    def test_successful_exit_and_original_tests_required(self):
        current = {**self.baseline, "app.py": b"def add(a,b): return a+b\n"}
        self.assertTrue(qualification(current,self.baseline,self.payload())["qualified"])
        for changes in ({"exit_code":1},{"exit_code":None},{"timed_out":True},
                        {"cleanup_confirmed":False},{"output_truncated":True},{"observed_test_count":0}):
            with self.subTest(changes=changes):
                self.assertFalse(qualification(current,self.baseline,self.payload(**changes))["qualified"])

    def test_test_deletion_replacement_shadow_and_config_change_denied(self):
        for path,value in (("test_app.py",b"# weakened tests"),("pyproject.toml",b"# disabled test discovery"),
                           ("tests/test_shadow.py",b"# shadowed tests")):
            with self.subTest(path=path):
                self.assertFalse(qualification({**self.baseline,path:value},self.baseline,self.payload())["qualified"])
        without_test = {k:v for k,v in self.baseline.items() if k!='test_app.py'}
        self.assertFalse(qualification(without_test,self.baseline,self.payload())["qualified"])
        self.assertFalse(qualification({"app.py":b"x"},{"app.py":b"x"},self.payload())["qualified"])

    def test_build_is_separate_from_test_semantics(self):
        payload=self.payload(profile_id="node_build",observed_test_count=None)
        qualified=qualification(self.baseline,self.baseline,payload)
        self.assertTrue(qualified["qualified"])
        self.assertIsNone(qualified["tests_observed"])
        with self.assertRaises(ValidationError):
            CodingTaskSpec(repository=RepoSelection(repo_id="owner/repo"),test_profiles=("node_build",))

    def test_supported_count_formats_remain_advisory(self):
        self.assertEqual(observed_test_count('python_unittest','','Ran 2 tests in 0.1s\nOK'),2)
        self.assertEqual(observed_test_count('python_pytest','2 passed in 0.1s',''),2)
        self.assertEqual(observed_test_count('node_npm_test','# tests 3\n# pass 3\n',''),3)
        self.assertEqual(observed_test_count('python_unittest','','Ran 0 tests\nOK'),0)
        self.assertEqual(observed_test_count('python_pytest','All tests passed!',''),0)

    def test_invalid_ref_converts_to_validation_error(self):
        with self.assertRaises(ValidationError): RepoSelection(repo_id='owner/repo',ref='../main')

if __name__=='__main__': unittest.main()
