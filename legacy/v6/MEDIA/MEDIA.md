# Search & Replace (sr) v6.0.0 - Media & Visual Resources

## 📝 Overview

This document catalogs all visual assets, illustrations, and media resources for the Search and Replace (sr) v6.0.0 project. These materials support understanding and documentation of the utility.

## 🎨 Illustrations

### Main Workflow Illustration

**File**: `sr-workflow.png`  
**Purpose**: Shows the complete workflow from file discovery through backup creation to rollback restoration  
**Key Elements**:
- File pattern matching and discovery
- Binary file detection layers
- Search and replace transformation
- Automatic backup creation
- Session-based storage
- One-command rollback

### System Architecture Diagram

**File**: `sr-architecture.png`  
**Purpose**: Detailed technical architecture showing all major components  
**Components**:
1. **Session Management Layer** - Session ID generation, timestamp tracking, metadata storage
2. **File Discovery Layer** - Recursive traversal, pattern matching, exclusion rules
3. **Binary Detection Layer** - Three-method detection (grep, file utility, fallback)
4. **Processing Layer** - Escaping, sed replacements, backup creation
5. **Backup & Rollback Layer** - Session directories, metadata files, restoration logic

### Before/After Transformation

**File**: `sr-before-after.png`  
**Purpose**: Visual representation of file transformations  
**Shows**:
- Original files with outdated patterns (red highlighting)
- Transformation process (central arrows)
- Updated files with new patterns (green highlighting)
- Examples: domain updates, function renames, configuration changes

### Safety & Rollback Timeline

**File**: `sr-safety-timeline.png`  
**Purpose**: Timeline visualization of safety features and rollback capability  
**Features Shown**:
- Multiple session checkpoints with dates/times
- Automatic backup triggers
- Metadata capture at each session
- Direct rollback to any previous point
- File integrity validation
- Permission preservation mechanisms

### Terminal Usage Example

**File**: `sr-terminal-example.png`  
**Purpose**: Realistic terminal output showing typical usage  
**Content**:
- Actual command examples with syntax highlighting
- Color-coded output messages
- [INFO] messages in blue
- [SUCCESS] messages in green
- [WARNING] messages in yellow
- [ERROR] messages in red
- Processing statistics and counters
- Session identifiers

### Feature Comparison Chart

**File**: `sr-comparison.png`  
**Purpose**: Comparison between different replacement approaches  
**Compared**:
1. Manual text replacement (basic, risky)
2. Simple find & replace tools (limited features)
3. Search & Replace (sr) v6.0.0 (enterprise-grade)

### Tool Badge/Icon

**File**: `sr-badge.png`  
**Purpose**: Logo and badge for project identification  
**Use Cases**:
- README badges
- Project documentation header
- Social media sharing
- Presentation slides

## 📚 Documentation Files

| File | Size | Purpose |
|------|------|----------|
| README.md | 14.9 KB | Main documentation and usage guide |
| CHANGELOG.md | 5.2 KB | Version history and release notes |
| EXAMPLES.md | 7.3 KB | Real-world usage scenarios |
| LICENSE | 1.1 KB | MIT License text |
| RELEASE_v6.0.0.md | 4.8 KB | Release notes and highlights |
| ILLUSTRATIONS.md | 3.4 KB | Illustrations gallery |
| MEDIA.md | This file | Media resources catalog |

## 🛡️ Technical Specifications

### Image Format
- **Format**: PNG (lossless compression)
- **Quality**: High-resolution for both screen and print
- **Color Space**: RGB for web display
- **Transparency**: Supported where applicable

### Image Usage Guidelines

#### For GitHub README
```markdown
![Description](image-filename.png)
```

#### For Markdown Documents
```markdown
![Alt Text](path/to/image.png)
*Image Caption*
```

#### For HTML
```html
<img src="image-filename.png" alt="Description" width="800" />
```

## 📚 Content Organization

### By Topic

**Getting Started**
- sr-badge.png - Quick identification
- sr-workflow.png - Main workflow overview
- sr-terminal-example.png - Usage examples

**Technical Deep Dive**
- sr-architecture.png - System components
- sr-safety-timeline.png - Safety mechanisms
- sr-before-after.png - Transformation examples

**Comparison & Evaluation**
- sr-comparison.png - Feature comparison

### By Audience

**End Users**
- sr-workflow.png
- sr-before-after.png
- sr-terminal-example.png
- sr-comparison.png

**Developers**
- sr-architecture.png
- sr-safety-timeline.png
- All of the above

**Managers/Decision Makers**
- sr-comparison.png
- sr-badge.png
- sr-workflow.png

## 📑 Where to Use These Resources

### GitHub Repository
- README.md - Main workflow and badge
- Wiki pages - Detailed architecture diagrams
- Discussions - Usage examples
- Issues - Troubleshooting guides

### External Documentation
- Blog posts - Feature highlights and comparisons
- Presentations - Architecture and workflow diagrams
- Training materials - Step-by-step workflows
- Product pages - Feature comparisons and badges

### Social Media
- Twitter/X - Badge and workflow images
- LinkedIn - Architecture and comparison charts
- GitHub social - Feature showcase images

## 🙋 Attribution

All illustrations are created for the Search & Replace (sr) project and available under the same MIT License as the software.

**Project**: Search & Replace (sr) v6.0.0  
**Author**: Mikhail Deynekin  
**Email**: mid1977@gmail.com  
**Repository**: https://github.com/paulmann/sr-search-replace  

## 🗣️ Feedback & Improvements

If you have suggestions for additional illustrations or improvements to existing ones, please:
1. Open an issue on GitHub
2. Create a discussion
3. Email mid1977@gmail.com

---

**Last Updated**: December 8, 2024  
**Illustrations Version**: 1.0  
**SR Version**: 6.0.0
