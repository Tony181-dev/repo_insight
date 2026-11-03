"""Test logic giao dien (to mau bash, to mau log) bang node; khong co node thi bo qua."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'repomap', 'web', 'js')
NODE = shutil.which('node')

DRIVER = '''
import { bashTokens } from './highlight_bash.js';
import { logClass, logHtml } from './build.js';
const input = JSON.parse(process.argv[2]);
console.log(JSON.stringify({
  tokens: input.bash.map(s => bashTokens(s)),
  html: input.log.map(s => logHtml(s)),
  cls: input.log.map(s => logClass(s)),
}));
'''


@unittest.skipUnless(NODE, 'needs node')
class WebJsTest(unittest.TestCase):
    def run_js(self, bash=(), log=()):
        with tempfile.TemporaryDirectory() as tmp:
            for name in os.listdir(JS):
                shutil.copy(os.path.join(JS, name), tmp)
            with open(os.path.join(tmp, 'package.json'), 'w') as f:
                f.write('{"type": "module"}')
            with open(os.path.join(tmp, 'driver.js'), 'w') as f:
                f.write(DRIVER)
            out = subprocess.run([NODE, 'driver.js', json.dumps({'bash': list(bash), 'log': list(log)})], cwd=tmp,
                                 capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def tokens(self, text):
        toks = self.run_js(bash=[text])['tokens'][0]
        self.assertEqual(''.join(s for _, s in toks), text)             # khong mat / them ky tu nao
        return toks

    def classes(self, text):
        """{doan text -> lop}; lop '' la chu thuong."""
        return {s.strip(): c for c, s in self.tokens(text) if s.strip()}

    def test_nested_quotes_in_command_substitution(self):
        toks = self.classes('MODULE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n')
        self.assertEqual(toks['MODULE'], 'as')
        self.assertEqual(toks['${BASH_SOURCE[0]}'], 'va')
        self.assertEqual(toks['&&'], 'pu')                              # khong phai chu trong chuoi
        self.assertEqual(toks['pwd'], 'bi')
        self.assertEqual(toks['dirname'], 'cmd')

    def test_only_command_position_is_a_command(self):
        toks = self.tokens('echo done make test\nmake install\nmake O=out -j8 2>/dev/null\n')
        cls = [(c, s) for c, s in toks if s.strip()]
        self.assertIn(('bi', 'echo'), cls)
        self.assertIn(('cmd', 'make'), cls)
        self.assertIn(('op', '-j8'), cls)
        self.assertIn(('pu', '2>'), cls)
        self.assertNotIn('kw', [c for c, s in toks if 'done' in s])     # "done" la tham so cua echo
        self.assertNotIn('cmd', [c for c, s in toks if 'install' in s or 'test' in s or 'O=out' in s])

    def test_keywords_functions_assignments(self):
        toks = self.classes('need() {\n  local v\n  for x in a b; do\n    if [ -z "$x" ]; then exit 2; fi\n  done\n}\n'
                            'function step { echo hi; }\nFOO=bar make\nexport PATH=/x\n')
        self.assertEqual(toks['need'], 'fn')
        self.assertEqual(toks['step'], 'fn')
        self.assertEqual(toks['function'], 'kw')
        self.assertEqual(toks['local'], 'kw')
        self.assertEqual(toks['v'], 'as')
        self.assertEqual(toks['x'], 'as')
        self.assertEqual(toks['fi'], 'kw')
        self.assertEqual(toks['FOO'], 'as')
        self.assertEqual(toks['PATH'], 'as')
        self.assertEqual(toks['make'], 'cmd')                           # sau gan bien van la vi tri lenh
        self.assertEqual(toks['-z'], 'op')

    def test_comments_strings_escapes(self):
        toks = self.classes('#!/usr/bin/env bash\n# it\'s a comment\necho "a \\"b\\" $x" \'$y\' a#b ${#z}\n')
        self.assertEqual(toks['#!/usr/bin/env bash'], 'sb')
        self.assertEqual(toks["# it's a comment"], 'cm')
        self.assertEqual(toks['\\"'], 'es')
        self.assertEqual(toks['$x'], 'va')
        self.assertEqual(toks["'$y'"], 'st')                            # nhay don: khong thay bien
        self.assertEqual(toks['a#b'], '')                               # '#' giua tu khong phai comment
        self.assertEqual(toks['${#z}'], 'va')

    def test_case_and_heredoc(self):
        toks = self.classes('case "$1" in\n  clean|all) rm -rf out ;;\n  *) step x ;;\nesac\n'
                            'cat <<EOF > f\nit\'s $JOBS\nEOF\necho after\n')
        self.assertEqual(toks['clean'], '')                             # mau, khong phai lenh
        self.assertEqual(toks['rm'], 'cmd')
        self.assertEqual(toks['step'], 'cmd')
        self.assertEqual(toks['esac'], 'kw')
        self.assertEqual(toks['$JOBS'], 'va')
        self.assertEqual(toks['echo'], 'bi')                            # dau ' trong heredoc khong nuot phan sau

    def test_never_loses_text_on_broken_input(self):
        for text in ('echo "unterminated $(', "x='", '$((1+', '${x', 'a)b(c', '`', 'cat <<', '<<EOF', '\\', '$',
                     '$(' * 500, '"$(' * 500, 'case x in', 'function'):
            self.tokens(text)

    def test_log_words(self):
        r = self.run_js(log=['Output: out/venc.img\n', '#### build completed successfully (02:10) ####\n',
                             'BUILD SUCCESSFUL in 3s\n', 'output_dir set\n', 'error: output file missing\n',
                             'error: build successfully skipped\n', 'build was not successful\n'])
        html, cls = r['html'], r['cls']
        self.assertIn('<mark class="lw-out">Output</mark>', html[0])
        self.assertIn('<mark class="lw-ok">build completed successfully</mark>', html[1])
        self.assertEqual(cls[1], 'ok')
        self.assertIn('<mark class="lw-ok">BUILD SUCCESSFUL</mark>', html[2])
        self.assertNotIn('<mark', html[3])                              # output_dir khong phai tu "output"
        self.assertIn('<mark class="lw-out">output</mark>', html[4])
        self.assertEqual(cls[4], 'err')
        self.assertNotIn('lw-ok', html[5])                              # dong loi: khong to xanh
        self.assertEqual(cls[6], 'err')                                 # "not successful" la that bai
        self.assertNotIn('lw-ok', html[6])


if __name__ == '__main__':
    unittest.main()
