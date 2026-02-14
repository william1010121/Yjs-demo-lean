import Lake
open Lake DSL

package «lean-project» where
  leanOptions := #[
    ⟨`autoImplicit, false⟩
  ]


@[default_target]
lean_lib «LeanProject» where
  srcDir := "."
  globs := #[.submodules `src]
