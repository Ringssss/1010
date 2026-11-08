"""Read installed Torch version/header without importing Torch or touching CUDA."""
import ast
import glob
import hashlib
import json
from pathlib import Path
import site
import sys
import sysconfig


def read_text(path):
    data = path.read_bytes()
    return data.decode('utf-8'), hashlib.sha256(data).hexdigest()


def span(lines, marker, next_marker=None, maximum=80):
    for index, line in enumerate(lines):
        if marker in line:
            end = min(index+maximum, len(lines))
            if next_marker:
                for following in range(index+1, end):
                    if next_marker in lines[following]:
                        end = following
                        break
            return dict(start_line=index+1, end_line=end,
                lines=[dict(line=i+1, text=lines[i]) for i in range(index,end)])
    return None


def main():
    paths = list(sys.path)
    try:
        paths += site.getsitepackages()
    except AttributeError:
        pass
    paths += [site.getusersitepackages()]
    paths += [value for key,value in sysconfig.get_paths().items() if key in ('purelib','platlib')]
    candidates, seen = [], set()
    for search in paths:
        if not search:
            continue
        for package in glob.glob(str(Path(search)/'torch')):
            root = Path(package).resolve()
            if str(root) in seen or not (root/'version.py').is_file():
                continue
            seen.add(str(root))
            row = dict(package_directory=str(root), search_path=str(search))
            version_path = root/'version.py'
            try:
                content, sha = read_text(version_path)
                values = {}
                for node in ast.parse(content).body:
                    if isinstance(node, ast.Assign):
                        names = [target.id for target in node.targets if isinstance(target,ast.Name)]
                        value_node=node.value
                    elif isinstance(node, ast.AnnAssign) and isinstance(node.target,ast.Name):
                        names, value_node = [node.target.id], node.value
                    else:
                        continue
                    for name in names:
                        if name in ('__version__','git_version','cuda','hip','debug'):
                            try:
                                values[name]=ast.literal_eval(value_node)
                            except (ValueError,TypeError):
                                values[name]=dict(unparsed_ast=ast.dump(value_node))
                row['version']=dict(path=str(version_path),sha256=sha,values=values)
            except Exception as error:
                row['version_error']=repr(error)
            header_path=root/'include/ATen/TensorIndexing.h'
            row['header_present']=header_path.is_file()
            if row['header_present']:
                try:
                    content,sha=read_text(header_path)
                    lines=content.splitlines()
                    row['header']=dict(path=str(header_path),sha256=sha,
                        applySlice=span(lines,'inline Tensor applySlice(', 'inline Tensor applySelect(',80),
                        optimization_note=span(lines,'// NOTE [ Setting `disable_slice_optimization`',
                            'inline Tensor get_item(',65),
                        get_item=span(lines,'inline Tensor get_item(', 'inline void set_item(',100))
                except Exception as error:
                    row['header_error']=repr(error)
            candidates.append(row)
    status='installed_header_read' if any(row.get('header') for row in candidates) else 'installed_header_not_found'
    print(json.dumps(dict(status=status,python=sys.version,executable=sys.executable,
        searched_paths=paths,candidates=candidates,torch_imported=False,gpu_api_calls=0,
        scope='filesystem reads only; paths are candidates ordered by Python search paths'),indent=2))
    if status!='installed_header_read':
        raise SystemExit(1)


if __name__=='__main__':
    main()
