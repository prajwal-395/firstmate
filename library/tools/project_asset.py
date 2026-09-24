import os

from library.tools.ren_refusal import RenRefusal

class ProjectAssetNotFoundError(RenRefusal):
    """Raised when a declared project asset cannot be found on disk.

    Every site raises with the message alone; the fix is uniform
    because every one faults a path a declaration names: put the file
    where the declaration says it is, then re-run.
    """

    def __init__(self, message: str, *, fix: str = "") -> None:
        super().__init__(
            what=message,
            why=("a declared project asset must exist on disk before "
                 "the run trusts it"),
            fix=(fix or "place the asset at the declared path (a flat "
                        "file, or <name>/index<ext> beside it), then "
                        "re-run"))

def resolve_project_asset(declared_path: str, project_folder: str | None) -> str:
    """Resolve a declared project asset path against the project folder.

    Accepts both:
    1. A flat file at the exact declared path.
    2. A directory named after the path (minus its extension) containing an
       'index{ext}' file. For example, a declaration of 'compositions/MyComp.tsx'
       can resolve to 'compositions/MyComp/index.tsx'.

    Raises ProjectAssetNotFoundError if neither exists.
    """
    if os.path.isabs(declared_path):
        base_path = os.path.normpath(declared_path)
    else:
        base_path = os.path.normpath(os.path.join(project_folder or "", declared_path))

    if os.path.isfile(base_path):
        return base_path

    root, ext = os.path.splitext(base_path)
    if os.path.isdir(root):
        index_path = os.path.join(root, f"index{ext}")
        if os.path.isfile(index_path):
            return index_path

    raise ProjectAssetNotFoundError(
        f"project asset '{declared_path}' not found "
        f"(checked flat file and {os.path.basename(root)}/index{ext})"
    )
