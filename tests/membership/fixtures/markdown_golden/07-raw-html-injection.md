<script>alert("xss")</script>

<style>body { display: none; }</style>

<p onclick="steal()">Click me</p>

<p style="color:red">Styled text</p>

<iframe src="https://evil.example.com"></iframe>

<a href="javascript:alert(1)">Bad link</a>

Normal paragraph survives.
