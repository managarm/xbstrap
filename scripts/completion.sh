#/usr/bin/env bash
_xbstrap_completions() {
	if [ "${#COMP_WORDS[@]}" == "2" ]; then
		COMPREPLY+=($(compgen -W "init runtool fetch checkout patch regenerate configure-tool compile-tool install-tool configure build archive download install" "${COMP_WORDS[1]}"))
		return
	elif [ "${#COMP_WORDS[@]}" == "3" ]; then
		case "${COMP_WORDS[1]}" in
			init)
				COMPREPLY+=($(compgen -d -S / -- "${COMP_WORDS[2]}"))
				COMPREPLY+=($(compgen -W "./ ../" -- "${COMP_WORDS[2]}"))
				compopt -o nospace
				;;
			configure-tool|compile-tool)
				local tools="$(xbstrap list-tools)"
				COMPREPLY+=($(compgen -W "${tools}" -- "${COMP_WORDS[2]}"))
				;;
			install-tool)
				if [[ ${COMP_WORDS[2]:0:1} =~ "-" ]]; then
					COMPREPLY+=($(compgen -W "--all --reconfigure" -- "${COMP_WORDS[2]}"))
				else
					local tools="$(xbstrap list-tools)"
					COMPREPLY+=($(compgen -W "${tools}" -- "${COMP_WORDS[2]}"))
				fi
				;;
			fetch|checkout|patch|regenerate)
				local tools="$(xbstrap list-tools)"
				COMPREPLY+=($(compgen -W "${tools}" -- "${COMP_WORDS[2]}"))
				local pkgs="$(xbstrap list-pkgs)"
				COMPREPLY+=($(compgen -W "${pkgs}" -- "${COMP_WORDS[2]}"))
				;;
			configure|build)
				local pkgs="$(xbstrap list-pkgs)"
				COMPREPLY+=($(compgen -W "${pkgs}" -- "${COMP_WORDS[2]}"))
				;;
			install)
				if [[ ${COMP_WORDS[2]:0:1} =~ "-" ]]; then
					COMPREPLY+=($(compgen -W "--all --reconfigure --rebuild" -- "${COMP_WORDS[2]}"))
				else
					local pkgs="$(xbstrap list-pkgs)"
					COMPREPLY+=($(compgen -W "${pkgs}" -- "${COMP_WORDS[2]}"))
				fi
				;;
			runtool)
				COMPREPLY+=($(compgen -W "--build" -- "${COMP_WORDS[2]}"))
				;;
		esac
		return
	fi
}

complete -F _xbstrap_completions xbstrap -o dirnames 3