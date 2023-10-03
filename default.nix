{ pkgs ? import <nixpkgs> {} }:
pkgs.poetry2nix.mkPoetryApplication {
  projectDir = ./.;
  preferWheels = true; # insecure....
  overrides = pkgs.poetry2nix.defaultPoetryOverrides.extend
    (self: super: {
      urllib3 = super.urllib3.overridePythonAttrs (
        old: {
          buildInputs = (old.buildInputs or [ ]) ++ [ super.hatchling ];
        });
    });
}