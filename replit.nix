{ pkgs }: {
  deps = [
    pkgs.nodejs_18
    pkgs.python311
    pkgs.postgresql
    pkgs.npm
    pkgs.yarn
    pkgs.git
    pkgs.wget
    pkgs.curl
  ];

  env = {
    PYTHON_ALCHEMY_SILENCE_UBER_WARNING = "1";
  };

  postInstall = ''
    # Install project dependencies
    npm install
    pip install -r requirements.txt

    # Setup database
    mkdir -p $HOME/.postgres

    echo "Integration Health Monitor environment ready"
    echo "Available tools:"
    echo "  - Node.js 18 (npm, yarn)"
    echo "  - Python 3.11 (pip)"
    echo "  - PostgreSQL (psql)"
    echo ""
    echo "Next steps:"
    echo "  1. Run 'npm run dev' to start development server"
    echo "  2. Run 'npm run migrate' to setup Supabase schema"
    echo "  3. Visit http://localhost:3000"
  '';
}
