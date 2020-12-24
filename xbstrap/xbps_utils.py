import tarfile

import plistlib
import zstandard

def read_repodata(path):
	with open(path, "rb") as zidx:
		dctx = zstandard.ZstdDecompressor()
		with dctx.stream_reader(zidx) as reader:
			with tarfile.open(fileobj=reader, mode="r|") as tar:
				for ent in tar:
					if ent.name != "index.plist":
						continue
					with tar.extractfile(ent) as idxpl:
						pkg_idx = plistlib.load(idxpl, fmt=plistlib.FMT_XML)
						return pkg_idx
