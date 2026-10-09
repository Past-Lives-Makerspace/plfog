# Class hero without the blue tint

Felix, 2026-10-09: "Remove the blueish gradient that are on the Hero Banner Images on the Class Detail pages."

## Acceptance criteria
- The class detail hero (public page and the admin preview, which share `templates/classes/public/detail.html`) no longer lays a navy gradient over the photo.
- The title, subtitle and byline that sit on the bottom of the photo stay readable: a neutral (black) shade covers only the bottom half.

## Out of scope
- Guild page and help page heroes (`.pl-guild-hero__overlay` in hub.css).
- The blurred backdrop that fills the bars around a tall photo.
