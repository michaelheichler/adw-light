import unittest

from in_memory_hook import PASSED, InMemoryHookCase


def inline(code):
    return 'python3 -c "' + code + '"'


class ReadOnlyInlinePython(InMemoryHookCase):
    def test_platform_name_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(inline("import platform; print(platform.system())")), PASSED)

    def test_folder_listing_and_cwd_passes(self):
        """Write a unit test for domain logic and algorithms."""
        self.assertEqual(self.bash(inline("import os; print(os.listdir('.'), os.getcwd())")), PASSED)

    def test_temp_folder_name_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(inline("import tempfile; print(tempfile.gettempdir())")), PASSED)

    def test_json_read_of_package_name_passes(self):
        """Write a unit test for domain logic and algorithms."""
        self.assertEqual(self.bash(inline("import json; print(json.load(open('package.json'))['name'])")), PASSED)

    def test_installed_package_version_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(inline("import importlib.metadata as m; print(m.version('pip'))")), PASSED)

    def test_executable_lookup_passes(self):
        """Write a unit test for domain logic and algorithms."""
        self.assertEqual(self.bash(inline("import shutil; print(shutil.which('git'))")), PASSED)

    def test_stdout_write_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(inline("import sys; sys.stdout.write('x')")), PASSED)

    def test_datetime_replace_passes(self):
        """Write a unit test for domain logic and algorithms."""
        self.assertEqual(self.bash(inline("import datetime; print(datetime.datetime.now().replace(microsecond=0))")), PASSED)

    def test_namedtuple_asdict_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(inline("import collections; P = collections.namedtuple('P', 'a'); print(P(1)._asdict())")), PASSED)

    def test_dict_copy_passes(self):
        """Write a unit test for domain logic and algorithms."""
        self.assertEqual(self.bash(inline("d = {'a': 1}; print(d.copy())")), PASSED)

    def test_stdin_read_and_string_replace_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(inline("import sys; print(sys.stdin.read().replace('a', 'b'))")), PASSED)

    def test_future_import_passes(self):
        """Write a unit test for domain logic and algorithms."""
        self.assertEqual(self.bash(inline("from __future__ import annotations")), PASSED)


if __name__ == "__main__":
    unittest.main()
