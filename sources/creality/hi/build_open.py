# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Breno
"""Build an open printer module with Cython3.1.6 and compatible ARM CPython3.9 headers.
Usage: python build_open.py SOURCE MODULE OUTPUT --zig PATH --headers SDK
No vendor private key is needed to replace a GPL bridge on your own root-accessible printer.
"""
import argparse,subprocess,sys,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('module');p.add_argument('output')
p.add_argument('--zig',required=True);p.add_argument('--headers',required=True);a=p.parse_args()
c=str(Path(a.output).with_suffix('.c'))
subprocess.run([sys.executable,'-m','cython','-3','--module-name',a.module,'-X','annotation_typing=False','-X','infer_types=False','-o',c,a.source],check=True)
if 'type[' in Path(a.source).read_text(encoding='utf-8-sig'):
 text=Path(c).read_text(encoding='utf8')
 pattern=r'static PyObject \*__Pyx_PyObject_GetItem\(PyObject \*obj, PyObject \*key\) \{.*?\n\}'
 text=re.sub(pattern,'static PyObject *__Pyx_PyObject_GetItem(PyObject *obj, PyObject *key) {\n    return PyObject_GetItem(obj, key);\n}',text,flags=re.S)
 Path(c).write_text(text,encoding='utf8')
subprocess.run([a.zig,'cc','-target','arm-linux-gnueabihf.2.29','-mcpu=cortex_a7','-shared','-fPIC','-O2','-I'+str(Path(a.headers)/'usr/include/python3.9'),'-I'+str(Path(a.headers)/'usr/include'),c,'-o',a.output],check=True)
