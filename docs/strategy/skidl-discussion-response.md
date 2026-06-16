# SKiDL Discussion Response Note

Context: Dave replied to the SKiDL discussion suggesting the layout, routing, and manufacturing tools should probably live outside SKiDL, especially while they are moving quickly and because they may be useful to non-SKiDL flows.

Do not reply immediately. Let the EDA-MCP / layout product shape evolve for a few days first.

## Positioning

- Agree with Dave that the layout/routing/manufacturing layer should likely remain external to SKiDL.
- Treat SKiDL as one strong input path, not the identity of the whole product.
- Keep upstream SKiDL contributions focused on core compatibility and circuit-authoring value, especially KiCad schematic generation and clean integration hooks.
- Frame the hosted MCP as the right architecture for an agent-native, iterative, toolchain-heavy PCB workflow, not merely as a way to recover compute costs.
- Be clear that pricing, if any, is intended to stay close to execution cost while funding hosting, reliability, bug fixes, and corpus-driven improvements.

## Draft Reply

```markdown
Thanks Dave, this is really helpful and broadly matches where my thinking has been landing.

I agree the layout/routing/manufacturing layer probably wants to live outside SKiDL, especially while it is moving quickly. I will keep upstreaming the pieces that are clearly SKiDL-core, like KiCad compatibility and schematic-generation improvements, but treat the layout/MCP/manufacturing workflow as a separate SKiDL-consuming tool.

I would also be very happy to give you access to the beta once it is stable enough to poke at. The intent is not to wall off SKiDL functionality; it is more that I think the correct interface for this class of tool is a hosted MCP/service because PCB generation is an iterative, stateful, toolchain-heavy workflow. The hosted version can provide stable tool contracts, isolated execution, known KiCad/Freerouting versions, previews, routing/DRC loops, telemetry, feedback turns, and eventually manufacturing integration.

Longer term, I think a small interchange contract between SKiDL and external EDA tools would be the cleanest boundary: SKiDL emits circuit/netlist/metadata/floorplan hints, external tools do placement/routing/manufacturing workflows, and results can be read back where useful.
```

## Notes

- Avoid sounding defensive about commercial intent.
- Avoid implying the hosted product is "paid SKiDL".
- Emphasize that the commercial surface is the external workflow and hosted capability.
- Mention beta access as a goodwill invitation, not a launch announcement.
- Do not overcommit to open sourcing the layout engine while the product boundary is still being discovered.
