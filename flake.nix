{
  description = "Flake for development of the scriptflow python package";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system: let
      
      pkgs = nixpkgs.legacyPackages.${system};

      # # Python packages
      # my-python-packages = ps: with ps; [
      #   pytest
      #   requests
      #   datetime
      #   omegaconf
      #   rich
      #   tinydb
      #   click
      # ];
      # my-python = pkgs.python3.withPackages my-python-packages;

    in {
      devShells.default = pkgs.mkShell {
        nativeBuildInputs = [ pkgs.bashInteractive ];
        buildInputs = [ 
            # my-python
            pkgs.python3
            pkgs.poetry
            pkgs.R
            pkgs.julia
        ];
       };
    });
}