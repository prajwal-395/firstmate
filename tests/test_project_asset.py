import os
import pytest
from library.tools.project_asset import resolve_project_asset, ProjectAssetNotFoundError

def test_resolve_flat_file(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    asset = project / "compositions" / "MyComp.tsx"
    asset.parent.mkdir()
    asset.touch()
    
    resolved = resolve_project_asset("compositions/MyComp.tsx", str(project))
    assert resolved == str(asset)

def test_resolve_directory_index(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    asset_dir = project / "compositions" / "MyComp"
    asset_dir.mkdir(parents=True)
    index = asset_dir / "index.tsx"
    index.touch()
    
    resolved = resolve_project_asset("compositions/MyComp.tsx", str(project))
    assert resolved == str(index)

def test_resolve_missing_raises(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    
    with pytest.raises(ProjectAssetNotFoundError) as exc:
        resolve_project_asset("compositions/MyComp.tsx", str(project))
        
    assert "project asset 'compositions/MyComp.tsx' not found" in str(exc.value)

