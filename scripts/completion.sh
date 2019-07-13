#/usr/bin/env bash
_xbstrap_completions() {
	if [ "${#COMP_WORDS[@]}" == "2" ]; then
		COMPREPLY+=($(compgen -W "init runtool fetch checkout patch regenerate configure-tool compile-tool install-tool configure build archive download install list-tools list-pkgs" -- "${COMP_WORDS[1]}"))
		return
	elif [ "${#COMP_WORDS[@]}" -ge "3" ]; then
		# check whether --all has been specified
		local all_specified=false

		for i in "${COMP_WORDS[@]:2}"; do
			if [ "$i" == "--all" ]; then
				all_specified=true
			fi
		done

		case "${COMP_WORDS[1]}" in
			init)
				COMPREPLY+=($(compgen -d -S / -- "${COMP_WORDS[${COMP_CWORD}]}"))
				COMPREPLY+=($(compgen -W "./ ../" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				compopt -o nospace
				;;
			configure-tool)
				if [[ ${COMP_WORDS[${COMP_CWORD}]:0:1} =~ "-" ]]; then
					COMPREPLY+=($(compgen -W "--all" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				elif [ $all_specified = false ]; then
					local tools="$(xbstrap list-tools)"
					COMPREPLY+=($(compgen -W "${tools}" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				fi
				;;
			compile-tool)
				if [[ ${COMP_WORDS[${COMP_CWORD}]:0:1} =~ "-" ]]; then
					COMPREPLY+=($(compgen -W "--all --reconfigure" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				elif [ $all_specified = false ]; then
					local tools="$(xbstrap list-tools)"
					COMPREPLY+=($(compgen -W "${tools}" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				fi

				;;
			install-tool)
				if [[ ${COMP_WORDS[${COMP_CWORD}]:0:1} =~ "-" ]]; then
					COMPREPLY+=($(compgen -W "--all --reconfigure --recompile" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				elif [ $all_specified = false ]; then
					local tools="$(xbstrap list-tools)"
					COMPREPLY+=($(compgen -W "${tools}" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				fi
				;;
			fetch|checkout|patch|regenerate)
				local tools="$(xbstrap list-tools)"
				local pkgs="$(xbstrap list-pkgs)"
				COMPREPLY+=($(compgen -W "${pkgs} ${tools}" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				;;
			configure)
				if [[ ${COMP_WORDS[${COMP_CWORD}]:0:1} =~ "-" ]]; then
					COMPREPLY+=($(compgen -W "--update --overwrite --all" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				elif [ $all_specified = false ]; then
					local pkgs="$(xbstrap list-pkgs)"
					COMPREPLY+=($(compgen -W "${pkgs}" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				fi
				;;
			build)
				if [[ ${COMP_WORDS[${COMP_CWORD}]:0:1} =~ "-" ]]; then
					COMPREPLY+=($(compgen -W "--update --overwrite --all --reconfigure" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				elif [ $all_specified = false ]; then
					local pkgs="$(xbstrap list-pkgs)"
					COMPREPLY+=($(compgen -W "${pkgs}" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				fi
				;;
			install)
				if [[ ${COMP_WORDS[${COMP_CWORD}]:0:1} =~ "-" ]]; then
					COMPREPLY+=($(compgen -W "--all --reconfigure --rebuild --update --overwrite" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				elif [ $all_specified = false ]; then
					local pkgs="$(xbstrap list-pkgs)"
					COMPREPLY+=($(compgen -W "${pkgs}" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				fi
				;;
			runtool)
				if [[ ${COMP_WORDS[${COMP_CWORD}]:0:1} =~ "-" ]]; then
					COMPREPLY+=($(compgen -W "--build" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				else
					local tools="$(xbstrap list-tools)"
					COMPREPLY+=($(compgen -W "${tools}" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				fi
				;;
			download|archive)
				if [[ ${COMP_WORDS[${COMP_CWORD}]:0:1} =~ "-" ]]; then
					COMPREPLY+=($(compgen -W "--all" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				elif [ $all_specified = false ]; then
					local pkgs="$(xbstrap list-pkgs)"
					COMPREPLY+=($(compgen -W "${pkgs}" -- "${COMP_WORDS[${COMP_CWORD}]}"))
				fi
				;;
		esac
		return
	fi
}

complete -F _xbstrap_completions xbstrap