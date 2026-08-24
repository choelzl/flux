Reply with ONE line
  DESIGN: {"name": "<short-name>", "style": "shared"|"per-op", "latency": <int>,
           "method": "<dominant method>", "methods": {"<op>": "<method>", ...}}
then the complete SystemVerilog in one ```verilog fence (all modules in the one
fence; a per-op design includes every requested operator's module).