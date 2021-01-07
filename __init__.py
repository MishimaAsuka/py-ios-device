

#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
path = os.path.dirname(sys.modules[__name__].__file__)
sys.path.insert(0, path)
from .core import main