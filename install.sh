#!/bin/bash
set -e

INSTALL_DIR="/usr/lib/portname"
BIN_PATH="/usr/bin/portname"
POLICY_DIR="/usr/share/polkit-1/actions"
DESKTOP_DIR="/usr/share/applications"

usage() {
    echo "Usage: $0 [--uninstall]"
    echo ""
    echo "Install or uninstall portname - Audio Port Renamer"
}

install() {
    echo "Installing portname..."

    # Copy Python package
    sudo mkdir -p "$INSTALL_DIR"
    sudo cp -r portname/ "$INSTALL_DIR/"

    # Create launcher script
    sudo tee "$BIN_PATH" > /dev/null << 'SCRIPT'
#!/bin/bash
exec python3 -c "import sys; sys.path.insert(0, '/usr/lib/portname'); from portname.cli import main; main()" "$@"
SCRIPT
    sudo chmod +x "$BIN_PATH"

    # Install polkit policy (for GUI pkexec)
    if [ -d "$POLICY_DIR" ]; then
        sudo cp data/com.github.portname.policy "$POLICY_DIR/"
    fi

    # Install desktop file
    sudo cp data/portname.desktop "$DESKTOP_DIR/"

    echo ""
    echo "Installed! You can now:"
    echo "  - Run 'portname' in a terminal to launch the GUI"
    echo "  - Find 'Portname' in your application menu"
    echo "  - Run 'portname list' to see your audio ports"
    echo "  - Run 'sudo portname rename <port> \"New Name\"' to rename a port"
}

uninstall() {
    echo "Uninstalling portname..."

    # Revert all renamed ports first
    if [ -f "$BIN_PATH" ]; then
        echo "Reverting any renamed ports..."
        sudo "$BIN_PATH" revert --all 2>/dev/null || true
    fi

    sudo rm -f "$BIN_PATH"
    sudo rm -rf "$INSTALL_DIR"
    sudo rm -f "$POLICY_DIR/com.github.portname.policy"
    sudo rm -f "$DESKTOP_DIR/portname.desktop"

    # Remove any leftover WirePlumber rename rules
    sudo rm -f /etc/wireplumber/wireplumber.conf.d/90-portname-*.conf /etc/wireplumber/main.lua.d/90-portname-*.lua

    echo "Uninstalled."
}

case "${1:-}" in
    --uninstall)
        uninstall
        ;;
    --help|-h)
        usage
        ;;
    "")
        install
        ;;
    *)
        echo "Unknown option: $1"
        usage
        exit 1
        ;;
esac
