# Disable file completions for all subcommands
complete -c xbstrap -f

# Packages
set -l package_commands configure build install pull-pack download archive
complete -c xbstrap -n "__fish_seen_subcommand_from $package_commands" \
	-f -a "(xbstrap list-pkgs)"

# Tools
set -l tool_commands configure-tool compile-tool install-tool download-tool-archive runtool
complete -c xbstrap -n "__fish_seen_subcommand_from $tool_commands" \
	-f -a "(xbstrap list-tools)"

# Source management
complete -c xbstrap -n "__fish_seen_subcommand_from fetch checkout patch regenerate" \
	-f -a "(xbstrap list-tools)" -a "(xbstrap list-pkgs)"

# Init
complete -c xbstrap -n "__fish_seen_subcommand_from init" \
	-a "(__fish_complete_directories)"

# Misc options
complete -c xbstrap -s h -l help -d "Print a short help text and exit"
complete -c xbstrap -s v -d "Enable verbose output"
